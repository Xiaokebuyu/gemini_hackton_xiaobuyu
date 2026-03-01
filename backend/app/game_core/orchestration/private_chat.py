"""Private Chat Coordinator — 4-step private NPC conversation pipeline.

Implements 设计规范 §7.4 (PrivateChatCoordinator).

4-step flow (simplified from NpcInteractionCoordinator 6-step):
  1. Setup   — validate NPC, write player message to SceneBus (private), build context
  2. NPC     — AgenticExecutor runs NPC tools (speak/emote/update_feeling/…)
  3. Options — static dialogue options from NPC profile
  4. Return  — aggregate results, time_cost = 1/6

GM observation (Step 3) and Teammate reactions (Step 4) are intentionally
absent — private conversations are not observable by third parties.

Decision record: D-N19 (narrative.md)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.game_core.content import WorldInstance
from app.game_core.narrative.context_builder import AgentContextBuilder, NpcFullContext, _profile_get
from app.game_core.narrative.context_window import ContextWindow, WindowMessage
from app.game_core.narrative.executor import AgenticExecutor
from app.game_core.narrative.memory_retriever import MemoryRetriever
from app.game_core.narrative.models import AgentResult
from app.game_core.orchestration.npc_interaction import (
    _build_static_dialogue_options,
    _extract_speech_text,
)
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer
from app.game_core.state.slices.scene import SceneEntry

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Result model
# ------------------------------------------------------------------


@dataclass(slots=True)
class PrivateChatResult:
    """Raw output of the 4-step private chat pipeline.

    SSE conversion is the app layer's responsibility
    (AgentOrchestrationService._private_chat_result_to_sse).

    No gm_result or teammate_results — private conversations are not
    observable by third parties (§7.4).
    """

    success: bool
    npc_id: str
    npc_result: AgentResult | None = None
    dialogue_options: list[dict[str, Any]] = field(default_factory=list)
    time_cost: float = 0.0          # §7.4: private chat = 1/6 格
    error: str | None = None
    graphize_candidates: list[WindowMessage] = field(default_factory=list)


# ------------------------------------------------------------------
# Coordinator
# ------------------------------------------------------------------


class PrivateChatCoordinator:
    """Orchestrates the 4-step private chat pipeline.

    Differences from NpcInteractionCoordinator (6-step):
    - Step 1: SceneEntry visibility="private", audience=["player", "npc:{npc_id}"]
    - Steps 3-4 (GM observation + Teammate reactions): removed
    """

    def __init__(
        self,
        executor: AgenticExecutor,
        world: WorldInstance,
        state: StateContainer,
        *,
        memory_retriever: MemoryRetriever | None = None,
    ) -> None:
        self._executor = executor
        self._world = world
        self._state = state
        self._memory_retriever = memory_retriever

    async def execute(
        self,
        npc_id: str,
        player_message: str,
        execute_command: Callable[[Command], ExecuteResult],
        *,
        context_window: ContextWindow | None = None,
        text_chunk_sink: Callable[[str], Awaitable[None]] | None = None,
    ) -> PrivateChatResult:
        """Run the 4-step private chat flow.

        Args:
            npc_id: Target NPC identifier.
            player_message: The player's dialogue input.
            execute_command: Callback to execute state-changing commands.
            context_window: Optional per-NPC conversation window (injected
                by app layer via InstanceManager).

        Returns:
            PrivateChatResult with raw NPC agent result and dialogue options.
        """
        builder = AgentContextBuilder(self._world, self._state)

        # ---- Step 1: Setup ----------------------------------------
        npc_full = await builder.build_npc_full_context(
            npc_id, memory_retriever=self._memory_retriever,
        )
        if npc_full is None:
            logger.warning("PrivateChatCoordinator: NPC not found: %s", npc_id)
            return PrivateChatResult(
                success=False, npc_id=npc_id, error="npc_not_found",
            )
        system_prompt = npc_full.system_prompt
        npc_layers = npc_full.layers
        npc_profile = self._world.characters.get(npc_id) if self._world.has_registry("characters") else None
        npc_tags = list(_profile_get(npc_profile, "tags", [])) if npc_profile is not None else []

        # Private: scene entry is only visible to player + this NPC
        if self._state.has_slice("scene"):
            self._state.scene.add_entry(SceneEntry(
                source="player",
                content=player_message,
                visibility="private",
                audience=["player", f"npc:{npc_id}"],
                tags=["speech"],
            ))

        npc_context = builder.build_agent_context(
            "npc", npc_id, execute_command=execute_command,
        )

        # ---- Step 2: NPC Agent Response ---------------------------
        npc_result: AgentResult | None = None
        history = _window_to_history(context_window) if context_window is not None else None
        try:
            npc_result = await self._executor.run_agentic(
                role="npc",
                context=npc_context,
                system_prompt=system_prompt,
                user_message=player_message,
                max_turns=3,
                conversation_history=history,
                context_layers=npc_layers,
                text_chunk_sink=text_chunk_sink,
                traits=npc_tags,
            )
        except Exception:
            logger.exception("PrivateChatCoordinator: NPC agent failed: %s", npc_id)
            return PrivateChatResult(success=False, npc_id=npc_id, error="agent_failed")

        npc_speech = _extract_speech_text(npc_result) if npc_result else "(NPC said nothing)"

        # Update ContextWindow with this exchange and detect overflow.
        graphize_candidates: list[WindowMessage] = []
        if context_window is not None:
            should1 = context_window.add_message(WindowMessage(
                role="user", content=player_message,
                token_count=_approx_tokens(player_message), metadata={},
            ))
            should2 = context_window.add_message(WindowMessage(
                role="model", content=npc_speech,
                token_count=_approx_tokens(npc_speech), metadata={},
            ))
            if should1 or should2:
                graphize_candidates = context_window.pop_oldest_for_graphize()

        # ---- Step 3: Dialogue Options (static; LLM-enhanced: N-6) -
        dialogue_options = _build_static_dialogue_options(
            self._world, self._state, npc_id,
        )

        # ---- Step 4: Return ---------------------------------------
        return PrivateChatResult(
            success=True,
            npc_id=npc_id,
            npc_result=npc_result,
            dialogue_options=dialogue_options,
            time_cost=1 / 6,
            graphize_candidates=graphize_candidates,
        )


# ------------------------------------------------------------------
# Module-level helpers (each module self-contained per project pattern)
# ------------------------------------------------------------------


def _window_to_history(window: ContextWindow) -> list[dict[str, Any]]:
    """Convert ContextWindow messages to Gemini conversation history format.

    Skips messages already flagged as graphized (compressed into knowledge graph).
    """
    return [
        {"role": msg.role, "parts": [{"text": msg.content}]}
        for msg in window.messages
        if not msg.is_graphized
    ]


def _approx_tokens(text: str) -> int:
    """Approximate token count (4 chars ≈ 1 token)."""
    return max(1, len(text) // 4)
