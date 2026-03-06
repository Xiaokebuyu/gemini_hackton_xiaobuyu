"""Application-layer Agent orchestration for NPC/GM/Teammate responses.

Sits outside game_core — calls AgenticExecutor with role-specific prompts
and converts AgentResult into SSE events for the streaming endpoints.
"""

from __future__ import annotations

import json
import logging
import random
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Mapping

from app.game_core.narrative.context_builder import AgentContextBuilder, NpcFullContext, TeammateFull, _profile_get
from app.game_core.narrative.context_window import ContextWindow, WindowMessage
from app.game_core.narrative.executor import AgenticExecutor
from app.game_core.narrative.instance_manager import InstanceManager, NPCInstance
from app.game_core.narrative.memory_retriever import MemoryRetriever
from app.game_core.narrative.models import AgentResult
from app.game_core.orchestration.models import PipelineResult, SSEEvent
from app.game_core.orchestration.npc_interaction import (
    NpcInteractionCoordinator,
    NpcInteractionResult,
    _extract_visible_reply_text,
    _resolve_dialogue_options,
    _should_teammate_respond,
)
from app.game_core.orchestration.private_chat import (
    PrivateChatCoordinator,
    PrivateChatResult,
)
from app.game_core.orchestration.shared_context import SharedContext
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateDelta
from app.game_core.state.slices.scene import SceneEntry

if TYPE_CHECKING:
    from app.game_core import ManagedSession

logger = logging.getLogger(__name__)

_DIALOGUE_FOLLOW_UP_ACTIONS = {"skill_check", "saving_throw", "contest", "investigate"}


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
        instance = await self._get_or_create_instance(session, npc_id)

        coordinator = NpcInteractionCoordinator(
            executor=self._executor,
            world=session.runtime.world,
            state=session.runtime.state,
            memory_retriever=self._memory_retriever,
            memory_writer=self._make_memory_writer(session),
        )
        try:
            result = await coordinator.execute_interaction(
                npc_id=npc_id,
                player_message=player_message,
                execute_command=_make_command_executor(session),
                intent=intent,
                instance=instance,
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
            if result.error == "invalid_agent_response":
                logger.warning(
                    "NPC interaction returned invalid agent response: %s reason=%s",
                    npc_id,
                    result.error_reason or "unknown_protocol_error",
                )
                return [
                    _npc_protocol_error_event(
                        npc_id,
                        result.error_reason or "unknown_protocol_error",
                    )
                ]
            logger.warning("NPC interaction agent failed: %s error=%s", npc_id, result.error)
            return [SSEEvent(
                event_type="npc_error",
                payload={"npc_id": npc_id, "code": result.error or "agent_failed"},
            )]

        await self._write_episode(session, npc_id, result.graphize_candidates)

        return _interaction_result_to_sse(
            result,
            action_dispatcher=session.runtime.action_dispatcher,
        )

    # ---- Private 4-step chat (no GM/teammate observation) ----

    async def run_private_chat(
        self,
        session: ManagedSession,
        npc_id: str,
        player_message: str,
        text_chunk_sink: Callable[[str], Awaitable[None]] | None = None,
    ) -> list[SSEEvent]:
        """Private chat 4-step pipeline (no GM/teammate observation) → SSE events."""
        instance = await self._get_or_create_instance(session, npc_id)
        coordinator = PrivateChatCoordinator(
            executor=self._executor,
            world=session.runtime.world,
            state=session.runtime.state,
            memory_retriever=self._memory_retriever,
            memory_writer=self._make_memory_writer(session),
        )
        try:
            result = await coordinator.execute(
                npc_id=npc_id,
                player_message=player_message,
                execute_command=_make_command_executor(session),
                instance=instance,
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
            if result.error == "invalid_agent_response":
                logger.warning(
                    "Private chat returned invalid agent response: %s reason=%s",
                    npc_id,
                    result.error_reason or "unknown_protocol_error",
                )
                return [
                    _npc_protocol_error_event(
                        npc_id,
                        result.error_reason or "unknown_protocol_error",
                    )
                ]
            logger.warning("Private chat agent failed: %s error=%s", npc_id, result.error)
            return [SSEEvent(
                event_type="npc_error",
                payload={"npc_id": npc_id, "code": result.error or "agent_failed"},
            )]

        await self._write_episode(session, npc_id, result.graphize_candidates)

        return _private_chat_result_to_sse(
            result,
            action_dispatcher=session.runtime.action_dispatcher,
        )

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
        instance = await self._get_or_create_instance(session, npc_id)
        context_window = instance.context_window if instance is not None else None
        memory_writer = self._make_memory_writer(session)
        active_directive: dict[str, Any] | None = None
        if instance is not None and state.has_slice("time"):
            active_directive = instance.consume_directive(state.time.absolute_tick())

        # Build system prompt + 7-layer context in one retriever call (N-7)
        npc_full = await builder.build_npc_full_context(
            npc_id,
            memory_retriever=self._memory_retriever,
            active_directive=active_directive,
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
            "npc",
            npc_id,
            execute_command=_make_command_executor(session),
            metadata={"memory_writer": memory_writer} if memory_writer is not None else None,
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

        if _is_protocol_error(result):
            _log_protocol_error(role="npc", character_id=npc_id, result=result)
            return [_npc_protocol_error_event(npc_id, _protocol_reason(result))]

        # Update ContextWindow with this exchange.
        if context_window is not None:
            context_window.add_message(WindowMessage(
                role="user", content=player_message,
                token_count=_approx_tokens(player_message), metadata={},
            ))
            visible_reply = _extract_visible_reply_text(result)
            if visible_reply:
                context_window.add_message(WindowMessage(
                    role="model", content=visible_reply,
                    token_count=_approx_tokens(visible_reply), metadata={},
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

    async def run_post_action_round(
        self,
        shared: SharedContext,
        result: PipelineResult,
        apply_delta: Callable[[StateDelta | None], None],
        event_sink: Callable[[SSEEvent], Awaitable[None]] | None = None,
    ) -> list[SSEEvent]:
        """Run the main-flow agent round after one successful engine action."""
        if not result.success:
            return []

        collected: list[SSEEvent] = []

        async def _emit(event: SSEEvent) -> None:
            collected.append(event)
            if event_sink is not None:
                await event_sink(event)

        async def _text_chunk_sink(chunk: str) -> None:
            await _emit(SSEEvent("text_chunk", {"text": chunk}))

        execute_command = _make_shared_command_executor(shared, apply_delta)

        gm_events = await self._generate_gm_reaction_from_shared(
            shared,
            result,
            text_chunk_sink=_text_chunk_sink,
        )
        for event in gm_events:
            shared.scene_bus.add_entry(
                {
                    "source": "GM",
                    "content": str(event.payload.get("content", "")),
                    "visibility": "public",
                    "tags": [event.event_type],
                }
            )
            await _emit(event)

        npc_events = await self._generate_npc_reactions_from_shared(
            shared,
            result,
            execute_command=execute_command,
            text_chunk_sink=_text_chunk_sink,
        )
        for event in npc_events:
            await _emit(event)

        teammate_events = await self._generate_teammate_reactions_from_shared(
            shared,
            result,
            execute_command=execute_command,
        )
        for event in teammate_events:
            await _emit(event)

        follow_up_options = await self._build_follow_up_dialogue_options_event(
            shared,
            result,
        )
        if follow_up_options is not None:
            await _emit(follow_up_options)

        return collected

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

    async def _generate_gm_reaction_from_shared(
        self,
        shared: SharedContext,
        result: PipelineResult,
        text_chunk_sink: Callable[[str], Awaitable[None]] | None = None,
    ) -> list[SSEEvent]:
        builder = AgentContextBuilder(shared.world, shared.state)
        context = builder.build_agent_context("gm")
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

    async def _build_follow_up_dialogue_options_event(
        self,
        shared: SharedContext,
        result: PipelineResult,
    ) -> SSEEvent | None:
        if result.action_type not in _DIALOGUE_FOLLOW_UP_ACTIONS:
            return None

        action_context = result.metadata.get("action_context")
        if not isinstance(action_context, Mapping):
            return None

        interaction_type = str(action_context.get("interaction_type") or "").strip()
        if interaction_type and interaction_type != "dialogue_option":
            return None

        npc_id = str(action_context.get("dialogue_npc_id") or "").strip()
        if not npc_id:
            return _dialogue_options_unavailable_event(
                None,
                code="missing_dialogue_context",
                message="当前对话上下文缺失，无法生成下一轮选项。",
            )

        builder = AgentContextBuilder(shared.world, shared.state)
        context = builder.build_agent_context("gm")
        user_message = json.dumps(
            {
                "action_type": result.action_type,
                "success": result.success,
                "npc_id": npc_id,
                "time_cost": result.time_cost,
                "narrative_hints": list(result.narrative_hints),
                "action_context": dict(action_context),
            },
            ensure_ascii=False,
            default=str,
        )
        try:
            agent_result = await self._executor.run_agentic(
                role="gm",
                context=context,
                system_prompt=builder.build_gm_dialogue_options_prompt(),
                user_message=user_message,
                max_turns=2,
                context_layers=builder.build_gm_context(
                    hints=list(result.narrative_hints),
                ),
            )
        except Exception:
            logger.exception("GM dialogue options Agent failed")
            return _dialogue_options_unavailable_event(
                npc_id,
                code="agent_error",
                message="对话继续了，但下一轮选项生成失败。",
            )

        options = _resolve_dialogue_options(
            shared.world,
            shared.state,
            npc_id,
            agent_result=agent_result,
            allow_static_fallback=False,
        )
        if not options:
            return _dialogue_options_unavailable_event(
                npc_id,
                code="empty_options",
                message="对话继续了，但下一轮选项未生成，请改用自由输入或结束对话。",
            )

        return SSEEvent(
            event_type="dialogue_options",
            payload={
                "npc_id": npc_id,
                "options": _serialize_dialogue_options(npc_id, options),
            },
        )

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

            if _is_protocol_error(agent_result):
                _log_protocol_error(
                    role="teammate",
                    character_id=member_id,
                    result=agent_result,
                )
                continue

            events.extend(_teammate_result_to_sse(member_id, agent_result))

        return events

    async def _generate_teammate_reactions_from_shared(
        self,
        shared: SharedContext,
        result: PipelineResult,
        *,
        execute_command: Callable[[Command], ExecuteResult],
    ) -> list[SSEEvent]:
        state = shared.state
        world = shared.world
        events: list[SSEEvent] = []

        if not state.has_slice("party"):
            return events
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
        builder = AgentContextBuilder(world, state)
        scene_entries = [
            entry.snapshot()
            for entry in shared.scene_bus.get_for_role("gm")
        ]

        for member_id in members:
            if not _should_teammate_respond(world, member_id, scene_entries=scene_entries):
                continue
            tm_full = await builder.build_teammate_full_context(
                member_id,
                memory_retriever=self._memory_retriever,
            )
            if tm_full is None:
                continue

            context = builder.build_agent_context(
                "teammate",
                member_id,
                execute_command=execute_command,
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

            if _is_protocol_error(agent_result):
                _log_protocol_error(
                    role="teammate",
                    character_id=member_id,
                    result=agent_result,
                )
                continue

            events.extend(_teammate_result_to_sse(member_id, agent_result))
            scene_entries = [
                entry.snapshot()
                for entry in shared.scene_bus.get_for_role("gm")
            ]

        return events

    async def _generate_npc_reactions_from_shared(
        self,
        shared: SharedContext,
        result: PipelineResult,
        *,
        execute_command: Callable[[Command], ExecuteResult],
        text_chunk_sink: Callable[[str], Awaitable[None]] | None = None,
    ) -> list[SSEEvent]:
        state = shared.state
        world = shared.world
        forced_npc_id = _dialogue_npc_id_from_result(result)
        nearby_npc_ids = self._collect_nearby_npcs(
            shared,
            focus_npc_id=forced_npc_id,
        )
        if not nearby_npc_ids:
            return []

        builder = AgentContextBuilder(world, state)
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
        scene_entries = [
            entry.snapshot()
            for entry in shared.scene_bus.get_for_role("gm")
        ]
        events: list[SSEEvent] = []
        memory_writer = self._make_memory_writer_for_world(world)

        for npc_id in nearby_npc_ids:
            if forced_npc_id != npc_id and not _should_character_respond(
                world,
                npc_id,
                scene_entries=scene_entries,
            ):
                continue
            instance = await self._get_or_create_instance_for_world(world, state, npc_id)
            context_window = instance.context_window if instance is not None else None
            active_directive: dict[str, Any] | None = None
            if instance is not None and state.has_slice("time"):
                active_directive = instance.consume_directive(state.time.absolute_tick())

            npc_full = await builder.build_npc_full_context(
                npc_id,
                memory_retriever=self._memory_retriever,
                active_directive=active_directive,
            )
            if npc_full is None:
                continue
            npc_profile = (
                world.characters.get(npc_id)
                if world.has_registry("characters")
                else None
            )
            npc_tags = list(_profile_get(npc_profile, "tags", [])) if npc_profile is not None else []
            context = builder.build_agent_context(
                "npc",
                npc_id,
                execute_command=execute_command,
                metadata=(
                    {"memory_writer": memory_writer}
                    if memory_writer is not None else None
                ),
            )
            history = _window_to_history(context_window) if context_window is not None else None

            try:
                agent_result = await self._executor.run_agentic(
                    role="npc",
                    context=context,
                    system_prompt=npc_full.system_prompt,
                    user_message=user_message,
                    max_turns=2,
                    conversation_history=history,
                    context_layers=npc_full.layers,
                    text_chunk_sink=text_chunk_sink,
                    traits=npc_tags,
                )
            except Exception:
                logger.exception("Nearby NPC reaction failed: %s", npc_id)
                continue

            if _is_protocol_error(agent_result):
                _log_protocol_error(
                    role="npc",
                    character_id=npc_id,
                    result=agent_result,
                )
                continue

            if context_window is not None:
                context_window.add_message(
                    WindowMessage(
                        role="user",
                        content=user_message,
                        token_count=_approx_tokens(user_message),
                        metadata={"kind": "post_action_reaction"},
                    )
                )
                visible_reply = _extract_visible_reply_text(agent_result)
                if visible_reply:
                    context_window.add_message(
                        WindowMessage(
                            role="model",
                            content=visible_reply,
                            token_count=_approx_tokens(visible_reply),
                            metadata={"kind": "post_action_reaction"},
                        )
                    )

            events.extend(_npc_result_to_sse(npc_id, agent_result))
            scene_entries = [
                entry.snapshot()
                for entry in shared.scene_bus.get_for_role("gm")
            ]

        return events

    async def _get_or_create_instance(
        self,
        session: ManagedSession,
        actor_id: str,
    ) -> NPCInstance | None:
        return await self._get_or_create_instance_for_world(
            session.runtime.world,
            session.runtime.state,
            actor_id,
        )

    async def _get_or_create_instance_for_world(
        self,
        world: Any,
        state: Any,
        actor_id: str,
    ) -> NPCInstance | None:
        if self._instance_manager is None:
            return None
        npc_directives = (
            state.narrative_plan.npc_directives
            if state.has_slice("narrative_plan")
            else None
        )
        current_tick = state.time.absolute_tick() if state.has_slice("time") else 0
        instance = self._instance_manager.get_or_create(
            actor_id,
            npc_directives=npc_directives,
            current_tick=current_tick,
        )
        await self._flush_pending_writebacks_for_world(world)
        return instance

    async def _flush_pending_writebacks(self, session: ManagedSession) -> None:
        await self._flush_pending_writebacks_for_world(session.runtime.world)

    async def _flush_pending_writebacks_for_world(self, world: Any) -> None:
        if self._instance_manager is None:
            return
        for pending in self._instance_manager.drain_pending_writebacks():
            await self._write_episode_for_world(world, pending.actor_id, pending.messages)

    async def _write_episode(
        self,
        session: ManagedSession,
        actor_id: str,
        messages: list[WindowMessage],
    ) -> None:
        await self._write_episode_for_world(session.runtime.world, actor_id, messages)

    async def _write_episode_for_world(
        self,
        world: Any,
        actor_id: str,
        messages: list[WindowMessage],
    ) -> None:
        if not messages:
            return
        graph = getattr(self._memory_retriever, "_graph", None)
        if graph is None:
            return
        try:
            await graph.write_episode(
                actor_id=actor_id,
                messages=messages,
                context={"world": world},
            )
        except Exception:
            logger.exception("write_episode failed for %s", actor_id)

    def _make_memory_writer(
        self,
        session: ManagedSession,
    ) -> Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any] | None]] | None:
        graph = getattr(self._memory_retriever, "_graph", None)
        remember = getattr(graph, "remember", None)
        if not callable(remember):
            return None

        async def _writer(
            actor_id: str,
            knowledge: str,
            context: dict[str, Any],
        ) -> dict[str, Any] | None:
            payload = dict(context)
            payload.setdefault("world", session.runtime.world)
            return await remember(actor_id=actor_id, knowledge=knowledge, context=payload)

        return _writer

    def _make_memory_writer_for_world(
        self,
        world: Any,
    ) -> Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any] | None]] | None:
        graph = getattr(self._memory_retriever, "_graph", None)
        remember = getattr(graph, "remember", None)
        if not callable(remember):
            return None

        async def _writer(
            actor_id: str,
            knowledge: str,
            context: dict[str, Any],
        ) -> dict[str, Any] | None:
            payload = dict(context)
            payload.setdefault("world", world)
            return await remember(actor_id=actor_id, knowledge=knowledge, context=payload)

        return _writer

    def _collect_nearby_npcs(
        self,
        shared: SharedContext,
        *,
        focus_npc_id: str | None = None,
    ) -> list[str]:
        state = shared.state
        if not state.has_slice("areas") or not state.has_slice("player"):
            return []
        current_area = state.player.current_area
        if not current_area or current_area not in state.areas.areas:
            return []
        current_location = state.player.current_location
        area_state = state.areas.areas[current_area]
        party_members: set[str] = set()
        if state.has_slice("party") and isinstance(state.party.members, dict):
            party_members = set(state.party.members.keys())
        if focus_npc_id and focus_npc_id not in party_members:
            focus_location = area_state.npc_locations.get(focus_npc_id)
            if focus_location is not None and (
                not current_location or focus_location in {None, current_location}
            ):
                return [focus_npc_id]
        nearby: list[str] = []
        for npc_id, location_id in area_state.npc_locations.items():
            if npc_id in party_members:
                continue
            if current_location and location_id not in {None, current_location}:
                continue
            nearby.append(npc_id)
        return nearby[:3]


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


def _is_protocol_error(result: AgentResult | None) -> bool:
    return bool(result is not None and result.metadata.get("status") == "protocol_error")


def _protocol_reason(result: AgentResult | None) -> str:
    if result is None:
        return "unknown_protocol_error"
    reason = result.metadata.get("reason")
    return str(reason) if isinstance(reason, str) and reason else "unknown_protocol_error"


def _log_protocol_error(
    *,
    role: str,
    character_id: str,
    result: AgentResult,
) -> None:
    logger.warning(
        "Agent protocol violation role=%s character=%s reason=%s tool_calls=%s text_present=%s",
        role,
        character_id,
        _protocol_reason(result),
        result.metadata.get("tool_call_names", []),
        result.metadata.get("text_present", False),
    )


def _npc_protocol_error_event(npc_id: str, reason: str) -> SSEEvent:
    return SSEEvent(
        event_type="npc_response_error",
        payload={
            "npc_id": npc_id,
            "code": "invalid_agent_response",
            "reason": reason,
        },
    )


def _make_command_executor(session: ManagedSession):
    """Build an execute_command callback for NPC/Teammate agent tools.

    DESIGN NOTE — why this bypasses TickCoordinator:
    NPC/Teammate tool calls (speak, emote, update_feeling, add_knowledge) are
    "intra-tick micro-operations" that happen *within* an ongoing player turn,
    not as independent player-initiated ticks.  Running them through
    TickCoordinator would double-count time, re-trigger settlement hooks, and
    pollute change_log with agent side-effects.

    Constraint: tools registered for NPC/Teammate roles MUST only produce
    relation/disposition/scene deltas. These deltas are recorded back into
    TickCoordinator.change_log so settlement hooks can still observe them,
    even though they bypass PipelineOrchestrator.
    """
    def _executor(command: Command) -> ExecuteResult:
        result = session.runtime.rules_engine.execute(
            command, session.runtime.state, session.runtime.world,
        )
        session.runtime.tick_coordinator.apply_external_result(result)
        return result
    return _executor


def _make_shared_command_executor(
    shared: SharedContext,
    apply_delta: Callable[[StateDelta | None], None],
):
    """Build an execute_command callback for main-flow agent rounds."""

    def _executor(command: Command) -> ExecuteResult:
        result = shared.rules_engine.execute(command, shared.state, shared.world)
        if result.success and result.delta is not None:
            apply_delta(result.delta)
        return result

    return _executor


def _should_character_respond(
    world: Any,
    char_id: str,
    *,
    scene_entries: list[dict[str, Any]] | None = None,
) -> bool:
    """Generic response gate for nearby NPCs in the main action pipeline."""
    profile = None
    if world.has_registry("characters"):
        profile = world.characters.get(char_id)
    tendency = 0.2
    if profile is not None:
        raw = _profile_get(profile, "response_tendency", 0.2)
        try:
            tendency = float(raw)
        except (TypeError, ValueError):
            tendency = 0.2

    if scene_entries:
        all_tags: set[str] = set()
        recent_speaks = 0
        for entry in scene_entries:
            if not isinstance(entry, dict):
                continue
            all_tags.update(entry.get("tags", []))
            if str(entry.get("source", "")) == char_id:
                recent_speaks += 1
        if "COMBAT_END" in all_tags:
            tendency += 0.2
        if "CRISIS" in all_tags:
            tendency += 0.3
        if "TRIVIAL" in all_tags:
            tendency -= 0.15
        tendency -= recent_speaks * 0.15

    return random.random() < max(0.05, min(0.95, tendency))


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


def _dialogue_npc_id_from_result(result: PipelineResult) -> str | None:
    action_context = result.metadata.get("action_context")
    if not isinstance(action_context, Mapping):
        return None
    npc_id = str(action_context.get("dialogue_npc_id") or "").strip()
    return npc_id or None


def _dialogue_options_unavailable_event(
    npc_id: str | None,
    *,
    code: str,
    message: str,
) -> SSEEvent:
    return SSEEvent(
        event_type="dialogue_options_unavailable",
        payload={
            "npc_id": npc_id,
            "code": code,
            "message": message,
            "recoverable": True,
        },
    )


def _dialogue_option_dispatch(
    npc_id: str,
    option: dict[str, Any],
    *,
    action_dispatcher: Any = None,
) -> dict[str, Any] | None:
    check = option.get("check")
    if isinstance(check, dict):
        skill = str(check.get("skill") or "").strip()
        dc = check.get("dc")
        if skill and isinstance(dc, int):
            return {
                "kind": "action",
                "payload": {
                    "action_type": "skill_check",
                    "params": {"skill": skill, "dc": dc},
                    "context": {
                        "dialogue_npc_id": npc_id,
                        "interaction_type": "dialogue_option",
                    },
                },
            }

    action = str(option.get("action") or "").strip()
    if action in {"leave", "farewell"}:
        return {"kind": "local", "payload": {"action": "leave_dialogue"}}

    intent = str(option.get("intent") or "").strip()
    if intent == "browse":
        return {
            "kind": "interact",
            "payload": {
                "intent": "browse",
                "target_kind": "npc",
                "target_id": npc_id,
            },
        }
    if intent == "farewell":
        return {"kind": "local", "payload": {"action": "leave_dialogue"}}

    item_id = str(option.get("item_id") or "").strip()
    quest_id = str(option.get("quest_id") or "").strip()
    count = option.get("count")
    if intent in {"buy", "sell", "inspect_item"} and item_id:
        payload: dict[str, Any] = {
            "intent": intent,
            "target_kind": "npc",
            "target_id": npc_id,
            "item_id": item_id,
        }
        if isinstance(count, int) and count > 0:
            payload["count"] = count
        return {"kind": "interact", "payload": payload}
    if intent in {"ask_quest", "ask_progress", "ask_location", "ask_requirements", "ask_reward"} and quest_id:
        return {
            "kind": "interact",
            "payload": {
                "intent": intent,
                "target_kind": "npc",
                "target_id": npc_id,
                "quest_id": quest_id,
            },
        }

    return None


def _serialize_dialogue_options(
    npc_id: str,
    options: list[dict[str, Any]],
    *,
    action_dispatcher: Any = None,
) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for raw in options:
        if not isinstance(raw, dict):
            continue
        entry = dict(raw)
        dispatch = _dialogue_option_dispatch(
            npc_id,
            entry,
            action_dispatcher=action_dispatcher,
        )
        if dispatch is not None:
            entry["dispatch"] = dispatch
        serialized.append(entry)
    return serialized


def _private_chat_result_to_sse(
    result: PrivateChatResult,
    *,
    action_dispatcher: Any = None,
) -> list[SSEEvent]:
    """Convert 4-step private chat result → ordered SSE events.

    Order: scene_change (optional) → NPC response → GM inner monologue
           (optional, introspective) → dialogue options.
    """
    events: list[SSEEvent] = []
    # Scene change (private sub-area created)
    if result.scene_id:
        events.append(SSEEvent(
            event_type="scene_change",
            payload={
                "location_id": result.scene_id,
                "location_name": result.scene_name,
                "background": "private",
                "transition": "fade",
                "ambient_preset": None,
                "ambient_override": None,
            },
        ))
    if result.npc_result is not None:
        events.extend(_npc_result_to_sse(result.npc_id, result.npc_result))
    # GM inner monologue (player's inner voice, not third-party narration)
    if result.gm_result is not None:
        for tr in result.gm_result.tool_results:
            if tr.success and tr.metadata.get("event_type") == "gm_comment" and tr.message:
                events.append(SSEEvent(
                    event_type="gm_comment",
                    payload={"content": tr.message, "tone": "introspective"},
                ))
    if result.dialogue_options:
        events.append(SSEEvent(
            event_type="dialogue_options",
            payload={
                "npc_id": result.npc_id,
                "options": _serialize_dialogue_options(
                    result.npc_id,
                    result.dialogue_options,
                    action_dispatcher=action_dispatcher,
                ),
            },
        ))
    return events


def _interaction_result_to_sse(
    result: NpcInteractionResult,
    *,
    action_dispatcher: Any = None,
) -> list[SSEEvent]:
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
            payload={
                "npc_id": result.npc_id,
                "options": _serialize_dialogue_options(
                    result.npc_id,
                    result.dialogue_options,
                    action_dispatcher=action_dispatcher,
                ),
            },
        ))

    return events
