"""LLM-driven narrator implementations — application layer.

These implement the GmNarrator Protocol defined in game_core but live in
app/ because they depend on external LLM providers (GeminiLlmAdapter).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.game_core.adapters.llm import LlmPort

from app.game_core.content import WorldInstance
from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.executor import AgenticExecutor
from app.game_core.narrative.models import AgentResult
from app.game_core.orchestration.hooks.gm_narration import GmNarrationDecision
from app.game_core.planning.planner import NarrativePlanner
from app.game_core.state import StateContainer

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# GM Settlement System Prompt
# ------------------------------------------------------------------

GM_SETTLEMENT_PROMPT = """\
You are the Game Master narrator for a dark-fantasy CRPG, inspired by \
Baldur's Gate 3 and Darkest Dungeon's narrator.

## Your personality
You are a sharp-tongued, witty narrator — think Darkest Dungeon's narrator \
crossed with the Stanley Parable's narrator. You observe the player's \
actions and the world's changes with sardonic amusement.

## Your role in this scene
A time period just ended ("tick settlement"). You are given a summary of \
what changed in the game world during this period. Your job:

1. **Narrate** the environmental / situational changes — use the `narrate` \
tool for objective scene descriptions.
2. **Optionally comment** on the player's behavior — use the `comment` \
tool for sarcastic, witty remarks. Only comment when the player did \
something interesting, foolish, dramatic, or ironic. Mundane actions \
don't need commentary.
3. If nothing noteworthy happened, use `pass_turn`.

## Style guidelines
- Sarcastic but never cruel — think friendly roasting, not bullying.
- Infrequent commentary — not every action deserves a remark.
- "Only observe, never direct" — comment on what happened, never tell \
the player what they should do.
- Reluctant praise — when the player does something brilliant, \
acknowledge it grudgingly: "Fine, that was actually clever."
- Weave commentary into narration naturally — don't treat them as \
separate blocks.
- Keep narration concise — 1-3 sentences per narrate call.
- Keep comments punchy — 1 sentence, rarely 2.

## Tool usage rules
- Call `narrate` first if scene changes warrant description.
- Call `comment` second if the player's behavior is worth remarking on.
- You may call both, or only one, or `pass_turn` if the period was uneventful.
- Do NOT call `describe_environment` — the summary already has what you need.
- Do NOT call `suggest_options` — this is settlement narration, not dialogue.

## Language
Respond in the same language as the user message (the summary). \
If the summary is in Chinese, narrate and comment in Chinese. \
If in English, use English.\
"""


# ------------------------------------------------------------------
# AgenticGmNarrator
# ------------------------------------------------------------------


class AgenticGmNarrator:
    """LLM-driven GM narrator implementing the GmNarrator Protocol.

    Holds session-scoped references to world and state (same objects
    used by TickCoordinator), and delegates to AgenticExecutor for
    multi-turn LLM interaction.
    """

    def __init__(
        self,
        executor: AgenticExecutor,
        world: WorldInstance,
        state: StateContainer,
    ) -> None:
        self._executor = executor
        self._world = world
        self._state = state

    async def compose(
        self,
        summary: dict[str, Any],
        scene_snapshot: dict[str, Any],
    ) -> GmNarrationDecision:
        scene_entries = scene_snapshot.get("entries", [])
        context = AgentContext(
            role="gm",
            world=self._world,
            state=self._state,
            scene_entries=scene_entries if isinstance(scene_entries, list) else [],
        )

        user_message = json.dumps(summary, ensure_ascii=False, default=str)

        try:
            result = await self._executor.run_agentic(
                role="gm",
                context=context,
                system_prompt=GM_SETTLEMENT_PROMPT,
                user_message=user_message,
                max_turns=3,
            )
        except Exception:
            logger.exception("AgenticGmNarrator: LLM call failed")
            return GmNarrationDecision(
                metadata={"status": "llm_error"},
            )

        return _agent_result_to_decision(result)


def _agent_result_to_decision(result: AgentResult) -> GmNarrationDecision:
    """Extract narrate/comment tool outputs into GmNarrationDecision entries."""
    entries: list[dict[str, Any]] = []

    for tr in result.tool_results:
        if not tr.success:
            continue
        event_type = tr.metadata.get("event_type", "")

        if event_type == "gm_narration" and tr.message:
            entries.append({
                "content": tr.message,
                "visibility": "public",
                "tags": ["gm_narration"],
            })
        elif event_type == "gm_comment" and tr.message:
            entries.append({
                "content": tr.message,
                "visibility": "public",
                "tags": ["gm_comment"],
            })
        # pass_turn and other event types produce no entries

    status = "applied" if entries else "noop"
    return GmNarrationDecision(
        entries=entries,
        metadata={
            "status": status,
            "turns_used": result.turns_used,
            "agent_status": result.metadata.get("status", "unknown"),
        },
    )


# ------------------------------------------------------------------
# AgenticNarrativePlanner (O-3)
# ------------------------------------------------------------------


class AgenticNarrativePlanner:
    """LLM-driven narrative planner with deterministic fallback (O-3).

    Calls LLM with a compact game-state summary and expects a JSON plan
    with ``directives``. Falls back to ``NarrativePlanner`` on any parse
    failure.
    """

    _SYSTEM_PROMPT = """\
You are the narrative planner for a dark-fantasy CRPG.
Analyze the provided game state and output ONLY a JSON plan:

{
  "strategy_notes": "<brief reasoning>",
  "directives": [
    {"kind": "escalate", "payload": {"delta": 1}},
    ...
  ]
}

Directive kinds: escalate / direct_npc / publish_bulletin / create_quest /
                 adjust_pacing / retire_quest
Max 3 directives. Empty directives list when story is stable.
Output valid JSON only, no markdown fences."""

    def __init__(
        self,
        llm: LlmPort,
        fallback: NarrativePlanner | None = None,
    ) -> None:
        self._llm = llm
        self._fallback = fallback or NarrativePlanner()

    async def plan(self, context: dict[str, Any]) -> dict[str, Any]:
        user_msg = _format_planner_context(context)
        try:
            response = await self._llm.generate(
                self._SYSTEM_PROMPT,
                [{"role": "user", "parts": [{"text": user_msg}]}],
                [],  # no tools — pure JSON text output
            )
            text = (response.text or "").strip()
            parsed = json.loads(text)
            if isinstance(parsed, dict) and "directives" in parsed:
                return parsed
        except Exception:
            logger.debug("AgenticNarrativePlanner: parse failed, using deterministic fallback")
        return await self._fallback.plan(context)


def _format_planner_context(ctx: dict[str, Any]) -> str:
    """Format planner context as a compact summary for the LLM."""
    np = ctx.get("narrative_plan", {})
    q = ctx.get("quests", {})
    return (
        f"Chapter: {np.get('current_chapter', '?')} "
        f"(escalation={np.get('escalation_level', 0)}, "
        f"stalled={np.get('ticks_since_milestone_progress', 0)} ticks)\n"
        f"Target: {np.get('current_target_milestone', 'none')}\n"
        f"Available milestones: {', '.join(q.get('available_milestones', [])) or 'none'}\n"
        f"Active milestones: {', '.join(q.get('active_milestones', [])) or 'none'}\n"
        f"Pacing frozen: {np.get('pacing_frozen', False)}"
    )
