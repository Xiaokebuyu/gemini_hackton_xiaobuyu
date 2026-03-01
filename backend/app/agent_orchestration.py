"""Application-layer Agent orchestration for NPC/GM/Teammate responses.

Sits outside game_core — calls AgenticExecutor with role-specific prompts
and converts AgentResult into SSE events for the streaming endpoints.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from app.game_core.narrative.context_builder import AgentContextBuilder, NpcFullContext, TeammateFull, _profile_get
from app.game_core.narrative.context_window import ContextWindow, WindowMessage
from app.game_core.narrative.executor import AgenticExecutor
from app.game_core.narrative.instance_manager import InstanceManager
from app.game_core.narrative.memory_retriever import MemoryRetriever
from app.game_core.narrative.models import AgentResult
from app.game_core.orchestration.models import PipelineResult, SSEEvent
from app.game_core.orchestration.npc_interaction import (
    NpcInteractionCoordinator,
    NpcInteractionResult,
)
from app.game_core.orchestration.private_chat import (
    PrivateChatCoordinator,
    PrivateChatResult,
)
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state.slices.scene import SceneEntry

if TYPE_CHECKING:
    from app.game_core import ManagedSession

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# AgentOrchestrationService
# ------------------------------------------------------------------


class AgentOrchestrationService:
    """Application-layer Agent orchestration.

    Stateless — receives session context per-call.  Holds a single
    AgenticExecutor whose RoleToolRegistry contains all 19 tools
    (GM 5 + NPC 8 + Teammate 6).
    """

    def __init__(
        self,
        executor: AgenticExecutor,
        *,
        memory_retriever: MemoryRetriever | None = None,
        instance_manager: InstanceManager | None = None,
    ) -> None:
        self._executor = executor
        self._memory_retriever = memory_retriever
        self._instance_manager = instance_manager

    # ---- Full 6-step NPC interaction ----

    async def run_npc_interaction(
        self,
        session: ManagedSession,
        npc_id: str,
        player_message: str,
        intent: str = "talk",
        text_chunk_sink: Callable[[str], Awaitable[None]] | None = None,
    ) -> list[SSEEvent]:
        """Full 6-step NPC interaction pipeline → SSE events.

        Delegates orchestration to NpcInteractionCoordinator (game_core)
        and converts the raw NpcInteractionResult to SSE events.
        """
        context_window = (
            self._instance_manager.get_or_create(npc_id)
            if self._instance_manager is not None else None
        )

        coordinator = NpcInteractionCoordinator(
            executor=self._executor,
            world=session.runtime.world,
            state=session.runtime.state,
            memory_retriever=self._memory_retriever,
        )
        try:
            result = await coordinator.execute_interaction(
                npc_id=npc_id,
                player_message=player_message,
                execute_command=_make_command_executor(session),
                intent=intent,
                context_window=context_window,
                text_chunk_sink=text_chunk_sink,
            )
        except Exception:
            logger.exception("NPC interaction pipeline failed: %s", npc_id)
            return [SSEEvent(
                event_type="npc_response_error",
                payload={"npc_id": npc_id, "error": "interaction_failed"},
            )]

        if not result.success:
            if result.error == "npc_not_found":
                logger.warning("NPC not found in interaction: %s", npc_id)
                return []
            logger.warning("NPC interaction agent failed: %s error=%s", npc_id, result.error)
            return [SSEEvent(
                event_type="npc_error",
                payload={"npc_id": npc_id, "code": result.error or "agent_failed"},
            )]

        # Overflow graphize — Phase 3b fills write_episode implementation.
        if result.graphize_candidates:
            graph = getattr(self._memory_retriever, "_graph", None)
            if graph is not None:
                try:
                    await graph.write_episode(
                        actor_id=npc_id,
                        messages=result.graphize_candidates,
                        context={"world": session.runtime.world},
                    )
                except Exception:
                    logger.exception("write_episode failed for %s", npc_id)

        return _interaction_result_to_sse(result)

    # ---- Private 4-step chat (no GM/teammate observation) ----

    async def run_private_chat(
        self,
        session: ManagedSession,
        npc_id: str,
        player_message: str,
        text_chunk_sink: Callable[[str], Awaitable[None]] | None = None,
    ) -> list[SSEEvent]:
        """Private chat 4-step pipeline (no GM/teammate observation) → SSE events."""
        context_window = (
            self._instance_manager.get_or_create(npc_id)
            if self._instance_manager is not None else None
        )
        coordinator = PrivateChatCoordinator(
            executor=self._executor,
            world=session.runtime.world,
            state=session.runtime.state,
            memory_retriever=self._memory_retriever,
        )
        try:
            result = await coordinator.execute(
                npc_id=npc_id,
                player_message=player_message,
                execute_command=_make_command_executor(session),
                context_window=context_window,
                text_chunk_sink=text_chunk_sink,
            )
        except Exception:
            logger.exception("Private chat pipeline failed: %s", npc_id)
            return [SSEEvent(
                event_type="npc_response_error",
                payload={"npc_id": npc_id, "error": "private_chat_failed"},
            )]

        if not result.success:
            if result.error == "npc_not_found":
                logger.warning("NPC not found in private chat: %s", npc_id)
                return []
            logger.warning("Private chat agent failed: %s error=%s", npc_id, result.error)
            return [SSEEvent(
                event_type="npc_error",
                payload={"npc_id": npc_id, "code": result.error or "agent_failed"},
            )]

        if result.graphize_candidates:
            graph = getattr(self._memory_retriever, "_graph", None)
            if graph is not None:
                try:
                    await graph.write_episode(
                        actor_id=npc_id,
                        messages=result.graphize_candidates,
                        context={"world": session.runtime.world},
                    )
                except Exception:
                    logger.exception("run_private_chat: write_episode failed for %s", npc_id)

        return _private_chat_result_to_sse(result)

    # ---- NPC dialogue (single-step, kept for backward compat) ----

    async def generate_npc_response(
        self,
        session: ManagedSession,
        npc_id: str,
        player_message: str,
        text_chunk_sink: Callable[[str], Awaitable[None]] | None = None,
    ) -> list[SSEEvent]:
        """Generate NPC dialogue response. Returns SSE events."""
        state = session.runtime.state
        world = session.runtime.world

        builder = AgentContextBuilder(world, state)

        # Build system prompt + 7-layer context in one retriever call (N-7)
        npc_full = await builder.build_npc_full_context(
            npc_id, memory_retriever=self._memory_retriever,
        )
        if npc_full is None:
            logger.warning("NPC profile not found: %s", npc_id)
            return []
        system_prompt = npc_full.system_prompt
        npc_profile = world.characters.get(npc_id) if world.has_registry("characters") else None
        npc_tags = list(_profile_get(npc_profile, "tags", [])) if npc_profile is not None else []

        # Write player message to SceneSlice
        if state.has_slice("scene"):
            state.scene.add_entry(SceneEntry(
                source="player",
                content=player_message,
                visibility="public",
                tags=["speech"],
            ))

        # Build AgentContext with command executor
        context = builder.build_agent_context(
            "npc", npc_id, execute_command=_make_command_executor(session),
        )

        # Retrieve or create per-NPC ContextWindow for conversation history.
        context_window: ContextWindow | None = (
            self._instance_manager.get_or_create(npc_id)
            if self._instance_manager is not None else None
        )
        history = _window_to_history(context_window) if context_window is not None else None

        # Call agentic executor with full 7-layer context (N-7)
        try:
            result = await self._executor.run_agentic(
                role="npc",
                context=context,
                system_prompt=system_prompt,
                user_message=player_message,
                max_turns=3,
                conversation_history=history,
                context_layers=npc_full.layers,
                text_chunk_sink=text_chunk_sink,
                traits=npc_tags,
            )
        except Exception:
            logger.exception("NPC Agent failed: %s", npc_id)
            return [SSEEvent(
                event_type="npc_response_error",
                payload={"npc_id": npc_id, "error": "agent_failed"},
            )]

        # Update ContextWindow with this exchange.
        if context_window is not None:
            context_window.add_message(WindowMessage(
                role="user", content=player_message,
                token_count=_approx_tokens(player_message), metadata={},
            ))
            if result.text:
                context_window.add_message(WindowMessage(
                    role="model", content=result.text,
                    token_count=_approx_tokens(result.text), metadata={},
                ))

        return _npc_result_to_sse(npc_id, result)

    # ---- Post-action GM + Teammate reactions ----

    async def generate_post_action_reactions(
        self,
        session: ManagedSession,
        result: PipelineResult,
        text_chunk_sink: Callable[[str], Awaitable[None]] | None = None,
    ) -> list[SSEEvent]:
        """Generate GM reaction + Teammate reactions after a player action."""
        events: list[SSEEvent] = []
        events.extend(await self._generate_gm_reaction(session, result, text_chunk_sink))
        events.extend(await self._generate_teammate_reactions(session, result))
        return events

    async def _generate_gm_reaction(
        self,
        session: ManagedSession,
        result: PipelineResult,
        text_chunk_sink: Callable[[str], Awaitable[None]] | None = None,
    ) -> list[SSEEvent]:
        """GM immediate reaction to a player action."""
        builder = AgentContextBuilder(session.runtime.world, session.runtime.state)
        context = builder.build_agent_context("gm")
        # Build GM 7-layer context with L7 narrative hints (N-7)
        gm_layers = builder.build_gm_context(hints=list(result.narrative_hints))

        user_message = json.dumps(
            {
                "action_type": result.action_type,
                "success": result.success,
                "narrative_hints": list(result.narrative_hints),
                "time_cost": result.time_cost,
            },
            ensure_ascii=False,
            default=str,
        )

        try:
            agent_result = await self._executor.run_agentic(
                role="gm",
                context=context,
                system_prompt=builder.build_gm_reaction_prompt(),
                user_message=user_message,
                max_turns=2,
                context_layers=gm_layers,
                text_chunk_sink=text_chunk_sink,
            )
        except Exception:
            logger.exception("GM reaction Agent failed")
            return []

        return _gm_result_to_sse(agent_result)

    async def _generate_teammate_reactions(
        self,
        session: ManagedSession,
        result: PipelineResult,
    ) -> list[SSEEvent]:
        """Teammate reactions after a player action."""
        state = session.runtime.state
        world = session.runtime.world
        events: list[SSEEvent] = []

        if not state.has_slice("party"):
            return events

        # members is dict[str, dict] — fix: was incorrectly checked as list
        members = state.party.members
        if not isinstance(members, dict) or not members:
            return events

        user_message = json.dumps(
            {
                "action_type": result.action_type,
                "success": result.success,
                "narrative_hints": list(result.narrative_hints),
            },
            ensure_ascii=False,
            default=str,
        )

        # Create builder once — reused across all teammates in this tick
        builder = AgentContextBuilder(world, state)

        for member_id in members:
            # Build system prompt + 7-layer context in one retriever call (N-7 Phase 2)
            tm_full = await builder.build_teammate_full_context(
                member_id, memory_retriever=self._memory_retriever,
            )
            if tm_full is None:
                continue

            context = builder.build_agent_context(
                "teammate", member_id,
                execute_command=_make_command_executor(session),
            )

            try:
                agent_result = await self._executor.run_agentic(
                    role="teammate",
                    context=context,
                    system_prompt=tm_full.system_prompt,
                    user_message=user_message,
                    max_turns=2,
                    context_layers=tm_full.layers,
                )
            except Exception:
                logger.exception("Teammate Agent failed: %s", member_id)
                continue

            events.extend(_teammate_result_to_sse(member_id, agent_result))

        return events


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _window_to_history(window: ContextWindow) -> list[dict[str, Any]]:
    """Convert ContextWindow messages to Gemini conversation history format.

    Skips messages already flagged as graphized.
    """
    return [
        {"role": msg.role, "parts": [{"text": msg.content}]}
        for msg in window.messages
        if not msg.is_graphized
    ]


def _approx_tokens(text: str) -> int:
    """Approximate token count (4 chars ≈ 1 token)."""
    return max(1, len(text) // 4)


def _make_command_executor(session: ManagedSession):
    """Build an execute_command callback for NPC/Teammate agent tools.

    DESIGN NOTE — why this bypasses TickCoordinator:
    NPC/Teammate tool calls (speak, emote, update_feeling, add_knowledge) are
    "intra-tick micro-operations" that happen *within* an ongoing player turn,
    not as independent player-initiated ticks.  Running them through
    TickCoordinator would double-count time, re-trigger settlement hooks, and
    pollute change_log with agent side-effects.

    Constraint: tools registered for NPC/Teammate roles MUST only produce
    relation/disposition/scene deltas.  Tools that would change inventory, HP,
    quests, or any state that settlement hooks depend on must NOT be added to
    NPC/Teammate role registries — use pipeline actions (player tick) instead.
    """
    def _executor(command: Command) -> ExecuteResult:
        result = session.runtime.rules_engine.execute(
            command, session.runtime.state, session.runtime.world,
        )
        if result.success and result.delta:
            session.runtime.state.apply(result.delta)
        return result
    return _executor


# ------------------------------------------------------------------
# Result → SSE converters
# ------------------------------------------------------------------


def _npc_result_to_sse(npc_id: str, result: AgentResult) -> list[SSEEvent]:
    """Convert NPC AgentResult tool_results into SSE events."""
    events: list[SSEEvent] = []

    for tr in result.tool_results:
        if not tr.success:
            continue
        event_type = tr.metadata.get("event_type", "")

        if event_type == "speech" and tr.message:
            events.append(SSEEvent(
                event_type="npc_response",
                payload={
                    "npc_id": npc_id,
                    "content": tr.message,
                    "type": "speech",
                },
            ))
        elif event_type == "emote" and tr.message:
            events.append(SSEEvent(
                event_type="npc_emote",
                payload={
                    "npc_id": npc_id,
                    "action": tr.message,
                    "type": "emote",
                },
            ))
        elif event_type == "refuse" and tr.message:
            events.append(SSEEvent(
                event_type="npc_response",
                payload={
                    "npc_id": npc_id,
                    "content": tr.message,
                    "type": "refuse",
                },
            ))
        # Command-based tools (update_feeling, remember, etc.) execute
        # via execute_command callback — no separate SSE needed.

    return events


def _gm_result_to_sse(result: AgentResult) -> list[SSEEvent]:
    """Convert GM AgentResult into SSE events."""
    events: list[SSEEvent] = []

    for tr in result.tool_results:
        if not tr.success:
            continue
        event_type = tr.metadata.get("event_type", "")

        if event_type == "gm_narration" and tr.message:
            events.append(SSEEvent(
                event_type="gm_narration",
                payload={"content": tr.message},
            ))
        elif event_type == "gm_comment" and tr.message:
            events.append(SSEEvent(
                event_type="gm_comment",
                payload={"content": tr.message},
            ))
        # pass_turn produces no event

    return events


def _teammate_result_to_sse(
    member_id: str, result: AgentResult,
) -> list[SSEEvent]:
    """Convert Teammate AgentResult into SSE events."""
    events: list[SSEEvent] = []

    for tr in result.tool_results:
        if not tr.success:
            continue
        event_type = tr.metadata.get("event_type", "")

        if event_type == "speech" and tr.message:
            events.append(SSEEvent(
                event_type="teammate_response",
                payload={
                    "character_id": member_id,
                    "content": tr.message,
                    "type": "speech",
                },
            ))
        elif event_type == "emote" and tr.message:
            events.append(SSEEvent(
                event_type="teammate_response",
                payload={
                    "character_id": member_id,
                    "action": tr.message,
                    "type": "emote",
                },
            ))
        # express_opinion, suggest_tactic, etc. — commands executed via
        # callback, pure text tools returned as metadata only.

    return events


def _private_chat_result_to_sse(result: PrivateChatResult) -> list[SSEEvent]:
    """Convert 4-step private chat result → ordered SSE events.

    No GM or teammate events — only NPC response + dialogue options.
    """
    events: list[SSEEvent] = []
    if result.npc_result is not None:
        events.extend(_npc_result_to_sse(result.npc_id, result.npc_result))
    if result.dialogue_options:
        events.append(SSEEvent(
            event_type="dialogue_options",
            payload={"npc_id": result.npc_id, "options": result.dialogue_options},
        ))
    return events


def _interaction_result_to_sse(result: NpcInteractionResult) -> list[SSEEvent]:
    """Convert NpcInteractionResult (6-step pipeline) into ordered SSE events.

    Order: NPC response → GM observation → Teammate reactions → Dialogue options.
    """
    events: list[SSEEvent] = []

    # Step 2: NPC response
    if result.npc_result is not None:
        events.extend(_npc_result_to_sse(result.npc_id, result.npc_result))

    # Step 3: GM observation
    if result.gm_result is not None:
        events.extend(_gm_result_to_sse(result.gm_result))

    # Step 4: Teammate reactions
    for member_id, tm_result in result.teammate_results.items():
        events.extend(_teammate_result_to_sse(member_id, tm_result))

    # Step 5: Dialogue options
    if result.dialogue_options:
        events.append(SSEEvent(
            event_type="dialogue_options",
            payload={"npc_id": result.npc_id, "options": result.dialogue_options},
        ))

    return events
