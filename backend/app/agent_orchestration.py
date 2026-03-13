"""Application-layer Agent orchestration for NPC/GM/Teammate responses.

Sits outside game_core — calls AgenticExecutor with role-specific prompts
and converts AgentResult into SSE events for the streaming endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import random
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Mapping

from app.game_core.clue_investigation import build_clue_dialogue_options
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
    RoundMessage,
    _build_capability_boundary_prompt,
    _extract_visible_reply_text,
    _resolve_dialogue_options,
    _should_teammate_respond,
)
from app.game_core.orchestration.private_chat import (
    PrivateChatCoordinator,
    PrivateChatResult,
)
from app.game_core.orchestration.presence import get_area_npcs, is_colocated
from app.game_core.orchestration.shared_context import SharedContext
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateDelta
from app.game_core.state.slices.scene import SceneEntry
from app.opening_views import build_opening_dialogue_options
from app.scene_views import build_location_overview

if TYPE_CHECKING:
    from app.game_core import ManagedSession

logger = logging.getLogger(__name__)

_DIALOGUE_FOLLOW_UP_ACTIONS = {"skill_check", "saving_throw", "contest", "investigate"}
_NPC_PASSIVE_SKIP_ACTIONS: set[str] = {
    "noop",
    "look_inventory",
    "equip",
    "unequip",
    "use_item",
    "drop_item",
    "check_stats",
    "check_quest_log",
    "check_map",
    "save_game",
    "load_game",
    "trade_buy",
    "trade_sell",
    "attack",
    "defend",
    "disengage",
    "rest_short",
    "rest_long",
}


# ------------------------------------------------------------------
# AgentOrchestrationService
# ------------------------------------------------------------------


@dataclass(slots=True)
class OpeningSequence:
    """GM-generated opening beat with per-part fallback support."""

    narration_event: SSEEvent | None = None
    comment_event: SSEEvent | None = None
    dialogue_options_event: SSEEvent | None = None


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
        check_result: dict[str, Any] | None = None,
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
            companion_manager=getattr(session.runtime, "companion_manager", None),
        )
        try:
            result = await coordinator.execute_interaction(
                npc_id=npc_id,
                player_message=player_message,
                execute_command=_make_command_executor(session),
                intent=intent,
                instance=instance,
                text_chunk_sink=text_chunk_sink,
                check_result=check_result,
            )
        except Exception:
            logger.exception("NPC interaction pipeline failed: %s", npc_id)
            return [SSEEvent(
                event_type="npc_response_error",
                payload={"npc_id": npc_id, "error": "interaction_failed"},
            )]

        if not result.completed:
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

        # Graphize path: triggered by graphize_counter reaching threshold in FIFO window
        instance_for_graphize = await self._get_or_create_instance(session, npc_id)
        context_window_for_graphize = (
            instance_for_graphize.context_window if instance_for_graphize is not None else None
        )
        if context_window_for_graphize is not None and context_window_for_graphize.should_graphize:
            messages_to_graphize = context_window_for_graphize.collect_for_graphize()
            if messages_to_graphize:
                await self._write_episode(session, npc_id, messages_to_graphize)
        elif result.graphize_candidates:
            # Legacy overflow path (graphize_candidates from NpcInteractionCoordinator)
            await self._write_episode(session, npc_id, result.graphize_candidates)

        # Write round record into each participating teammate's ContextWindow
        _write_teammate_context_windows(session, result)

        events = _interaction_result_to_sse(
            result,
            action_dispatcher=session.runtime.action_dispatcher,
        )
        if not _has_gm_comment_event(events):
            fallback = SSEEvent(
                event_type="gm_comment",
                payload={"content": "你把话抛了出去，仿佛先开口本身就算半场胜利。"},
            )
            events = _insert_event_before(
                events,
                fallback,
                before_event_types={"teammate_response", "dialogue_options"},
            )
        return events

    # ---- Free chat with party (no NPC target) ----

    async def run_public_utterance(
        self,
        session: ManagedSession,
        player_message: str,
        intent: str = "talk",
        text_chunk_sink: Callable[[str], Awaitable[None]] | None = None,
        check_result: dict[str, Any] | None = None,
    ) -> list[SSEEvent]:
        """Public untargeted speech — GM + teammate reactions, no focused NPC."""

        del text_chunk_sink  # Public untargeted speech currently has no streamed speaker.
        coordinator = NpcInteractionCoordinator(
            executor=self._executor,
            world=session.runtime.world,
            state=session.runtime.state,
            memory_retriever=self._memory_retriever,
            companion_manager=getattr(session.runtime, "companion_manager", None),
        )
        try:
            result = await coordinator.execute_public_utterance(
                player_message=player_message,
                execute_command=_make_command_executor(session),
                intent=intent,
                check_result=check_result,
            )
        except Exception:
            logger.exception("Public utterance pipeline failed")
            return [SSEEvent(
                event_type="stream_error",
                payload={"error": "public_utterance_failed"},
            )]

        _write_teammate_context_windows(session, result)
        events = _public_utterance_result_to_sse(result)
        if not _has_gm_comment_event(events):
            events = _insert_event_before(
                events,
                SSEEvent(
                    event_type="gm_comment",
                    payload={"content": "你的话落进空气里，而空气通常比措辞更诚实。"},
                ),
                before_event_types={"teammate_response"},
            )
        return events

    async def run_free_chat(
        self,
        session: ManagedSession,
        player_message: str,
    ) -> list[SSEEvent]:
        """Free party chat — teammate evaluation only, no NPC/GM.

        Delegates to NpcInteractionCoordinator.execute_free_chat which runs
        only Steps 4-5 of the interaction pipeline (serialized teammate
        evaluation + generic dialogue options).
        """
        coordinator = NpcInteractionCoordinator(
            executor=self._executor,
            world=session.runtime.world,
            state=session.runtime.state,
            memory_retriever=self._memory_retriever,
            companion_manager=getattr(session.runtime, "companion_manager", None),
        )
        try:
            result = await coordinator.execute_free_chat(
                player_message=player_message,
                execute_command=_make_command_executor(session),
            )
        except Exception:
            logger.exception("Free chat pipeline failed")
            return [SSEEvent(
                event_type="stream_error",
                payload={"error": "free_chat_failed"},
            )]

        # Write round record into each participating teammate's ContextWindow
        _write_teammate_context_windows(session, result)

        events = _free_chat_result_to_sse(result)
        comment_event = await self._build_party_comment_event(
            session,
            player_message,
            result,
        )
        if comment_event is not None:
            events.append(comment_event)
        return events

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

        if not result.completed:
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

        # Graphize path: triggered by graphize_counter reaching threshold in FIFO window
        instance_for_graphize_pc = await self._get_or_create_instance(session, npc_id)
        cw_for_graphize_pc = (
            instance_for_graphize_pc.context_window if instance_for_graphize_pc is not None else None
        )
        if cw_for_graphize_pc is not None and cw_for_graphize_pc.should_graphize:
            messages_to_graphize_pc = cw_for_graphize_pc.collect_for_graphize()
            if messages_to_graphize_pc:
                await self._write_episode(session, npc_id, messages_to_graphize_pc)
        elif result.graphize_candidates:
            # Legacy overflow path
            await self._write_episode(session, npc_id, result.graphize_candidates)

        events = _private_chat_result_to_sse(
            result,
            action_dispatcher=session.runtime.action_dispatcher,
            current_location=session.runtime.state.player.current_location,
        )
        if not _has_gm_comment_event(events):
            events = _insert_event_before(
                events,
                SSEEvent(
                    event_type="gm_comment",
                    payload={
                        "content": "你听见心里那点回声，比嘴上那句话更先承认了分量。",
                        "tone": "introspective",
                    },
                ),
                before_event_types={"dialogue_options"},
            )
        return events

    async def _build_party_comment_event(
        self,
        session: ManagedSession,
        player_message: str,
        result: NpcInteractionResult,
    ) -> SSEEvent | None:
        """Return one short GM comment for explicit party chat."""
        builder = AgentContextBuilder(session.runtime.world, session.runtime.state)
        gm_context = builder.build_agent_context("gm")
        visible_replies = _visible_teammate_reply_summaries(result)
        observation = json.dumps(
            {
                "interaction_type": "party_chat",
                "player_message": player_message,
                "audience_member_ids": list(result.audience_member_ids),
                "party_silent": not visible_replies,
                "teammate_replies": visible_replies,
            },
            ensure_ascii=False,
        )
        try:
            gm_result = await self._executor.run_agentic(
                role="gm",
                context=gm_context,
                system_prompt=builder.build_gm_party_chat_prompt(),
                user_message=observation,
                max_turns=1,
                context_layers=builder.build_gm_context(),
            )
        except Exception:
            logger.exception("GM party chat comment generation failed")
            gm_result = None

        comment_event = _first_gm_comment_event(gm_result)
        if comment_event is not None:
            return comment_event
        if visible_replies:
            return SSEEvent(
                event_type="gm_comment",
                payload={"content": "队伍总算把气氛接住了，虽然接法离体面还差一点。"},
            )
        return SSEEvent(
            event_type="gm_comment",
            payload={"content": "你的战术讨论收获颇丰：一阵足以切开的沉默。"},
        )

    async def generate_opening_sequence(
        self,
        session: ManagedSession,
    ) -> OpeningSequence:
        """Generate the GM opening beat for a new-game session."""
        builder = AgentContextBuilder(session.runtime.world, session.runtime.state)
        context = builder.build_agent_context("gm")
        user_message = _build_opening_user_message(session)

        try:
            agent_result = await self._executor.run_agentic(
                role="gm",
                context=context,
                system_prompt=builder.build_gm_opening_prompt(),
                user_message=user_message,
                max_turns=2,
                context_layers=builder.build_gm_opening_context(),
            )
        except Exception:
            logger.exception("GM opening Agent failed")
            return OpeningSequence()

        narration_event: SSEEvent | None = None
        comment_event: SSEEvent | None = None
        for event in _gm_result_to_sse(agent_result):
            if event.event_type == "gm_narration" and narration_event is None:
                narration_event = event
            elif event.event_type == "gm_comment" and comment_event is None:
                comment_event = event

        return OpeningSequence(
            narration_event=narration_event,
            comment_event=comment_event,
            dialogue_options_event=_build_opening_dialogue_options_event(
                session,
                agent_result,
            ),
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

        # QF-5: Inject capability boundary so NPC knows what it can/cannot do
        # Also inject dynamic capabilities assigned by the narrative planner
        dynamic_caps: list[dict] = []
        if state.has_slice("narrative_plan"):
            dynamic_caps = state.narrative_plan.get_capabilities(npc_id)
        system_prompt += _build_capability_boundary_prompt(self._executor, npc_tags, capabilities=dynamic_caps)

        # Write player message to SceneSlice
        if state.has_slice("scene"):
            state.scene.add_entry(SceneEntry(
                source="player",
                content=player_message,
                visibility="public",
                tags=["speech"],
            ))

        # Build AgentContext with command executor
        npc_metadata: dict[str, Any] = {
            "memory_retriever": self._memory_retriever,
            "world": session.runtime.world,
        }
        if memory_writer is not None:
            npc_metadata["memory_writer"] = memory_writer
        # Inject npc_tags so tools like AssignQuestTool can check trait constraints
        npc_metadata["npc_tags"] = npc_tags
        context = builder.build_agent_context(
            "npc",
            npc_id,
            execute_command=_make_command_executor(session),
            metadata=npc_metadata,
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
        if not result.executed:
            return []

        collected: list[SSEEvent] = []

        async def _emit(event: SSEEvent) -> None:
            collected.append(event)
            if event_sink is not None:
                await event_sink(event)

        async def _text_chunk_sink(chunk: str) -> None:
            await _emit(SSEEvent("text_chunk", {"text": chunk}))

        execute_command = _make_shared_command_executor(shared, apply_delta)
        if result.action_type == "investigate_clue":
            clue_events = await self._run_clue_investigation_round(
                shared,
                result,
                execute_command=execute_command,
            )
            for event in clue_events:
                await _emit(event)
            return collected
        if result.action_type == "resolve_clue_option":
            resolution_event = self._build_clue_resolution_comment_event(result)
            if resolution_event is not None:
                shared.scene_bus.add_entry(
                    {
                        "source": "GM",
                        "content": str(resolution_event.payload.get("content", "")),
                        "visibility": "public",
                        "tags": [resolution_event.event_type, "clue_resolution"],
                    }
                )
                await _emit(resolution_event)
            return collected

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

    async def _run_clue_investigation_round(
        self,
        shared: SharedContext,
        result: PipelineResult,
        *,
        execute_command: Callable[[Command], ExecuteResult],
    ) -> list[SSEEvent]:
        metadata = result.metadata if isinstance(result.metadata, Mapping) else {}
        interactable_id = str(metadata.get("interactable_id") or "").strip()
        clue_payload = {
            "clue_id": str(metadata.get("clue_id") or interactable_id).strip(),
            "interactable_id": interactable_id,
            "clue_name": str(metadata.get("clue_name") or metadata.get("name") or interactable_id or "线索").strip(),
            "description": str(metadata.get("description") or "").strip(),
            "topic": str(metadata.get("topic") or "").strip(),
            "linked_quest_id": str(metadata.get("linked_quest_id") or "").strip(),
            "linked_milestone": str(metadata.get("linked_milestone") or "").strip(),
            "party_prompt_hints": list(metadata.get("party_prompt_hints", []))
            if isinstance(metadata.get("party_prompt_hints"), list)
            else [],
            "options": [
                dict(option)
                for option in metadata.get("options", [])
                if isinstance(option, Mapping)
            ],
            "first_inspect_applied": bool(metadata.get("first_inspect_applied", False)),
            "area_id": str(metadata.get("area_id") or "").strip(),
            "location_id": str(metadata.get("location_id") or "").strip(),
            "room_id": str(metadata.get("room_id") or "").strip(),
        }
        if not clue_payload["clue_id"]:
            clue_payload["clue_id"] = interactable_id or "scene_clue"

        shared.scene_bus.add_entry(
            {
                "source": "ENGINE",
                "content": clue_payload["description"] or clue_payload["clue_name"],
                "visibility": "public",
                "tags": ["CLUE", "clue_investigation", "party_discussion"],
            }
        )

        events: list[SSEEvent] = []
        gm_events = await self._generate_clue_gm_events(shared, result, clue_payload)
        if not gm_events:
            gm_events = [self._build_fallback_clue_comment_event(clue_payload)]
        for event in gm_events:
            if event.event_type in {"gm_comment", "gm_narration"}:
                shared.scene_bus.add_entry(
                    {
                        "source": "GM",
                        "content": str(event.payload.get("content", "")),
                        "visibility": "public",
                        "tags": [event.event_type, "clue_investigation"],
                    }
                )
            events.append(event)

        teammate_events = await self._generate_clue_teammate_events(
            shared,
            clue_payload,
            gm_events=gm_events,
            execute_command=execute_command,
        )
        for event in teammate_events:
            if event.event_type == "teammate_response":
                shared.scene_bus.add_entry(
                    {
                        "source": f"TEAMMATE:{event.payload.get('character_id', '')}",
                        "content": str(event.payload.get("content") or event.payload.get("action") or ""),
                        "visibility": "public",
                        "tags": [event.event_type, "clue_investigation"],
                    }
                )
            events.append(event)

        if interactable_id:
            options = build_clue_dialogue_options(interactable_id, clue_payload)
            if options:
                events.append(
                    SSEEvent(
                        event_type="dialogue_options",
                        payload={"options": options},
                    )
                )

        return events

    async def _generate_clue_gm_events(
        self,
        shared: SharedContext,
        result: PipelineResult,
        clue_payload: Mapping[str, Any],
    ) -> list[SSEEvent]:
        builder = AgentContextBuilder(shared.world, shared.state)
        context = builder.build_agent_context("gm")
        gm_layers = builder.build_gm_context(
            hints=[*list(result.narrative_hints), "clue_investigation"],
        )
        user_message = json.dumps(
            {
                **_result_decision_payload(result),
                "interaction_type": "clue_investigation",
                "clue": dict(clue_payload),
            },
            ensure_ascii=False,
            default=str,
        )
        try:
            agent_result = await self._executor.run_agentic(
                role="gm",
                context=context,
                system_prompt=builder.build_gm_clue_prompt(),
                user_message=user_message,
                max_turns=2,
                context_layers=gm_layers,
            )
        except Exception:
            logger.exception("GM clue investigation Agent failed")
            return []
        return _gm_result_to_sse(agent_result)

    async def _generate_clue_teammate_events(
        self,
        shared: SharedContext,
        clue_payload: Mapping[str, Any],
        *,
        gm_events: list[SSEEvent],
        execute_command: Callable[[Command], ExecuteResult],
    ) -> list[SSEEvent]:
        state = shared.state
        if not state.has_slice("party"):
            return []
        members = state.party.members
        if not isinstance(members, dict) or not members:
            return []

        world = shared.world
        builder = AgentContextBuilder(world, state)
        companion_manager = shared.companion_manager
        current_tick = state.time.absolute_tick() if state.has_slice("time") else 0
        member_ids = [member_id for member_id in members if str(member_id).strip()]
        if companion_manager is not None:
            companion_manager.sync_members(member_ids, current_tick=current_tick)

        events: list[SSEEvent] = []
        responded_members = 0
        gm_observation = _first_visible_gm_text(gm_events)
        user_message = json.dumps(
            {
                "interaction_type": "clue_investigation",
                "clue": dict(clue_payload),
                "gm_observation": gm_observation,
                "available_options": [
                    {
                        "id": str(option.get("id", "")),
                        "label": str(option.get("label", "")),
                    }
                    for option in clue_payload.get("options", [])
                    if isinstance(option, Mapping)
                ],
            },
            ensure_ascii=False,
            default=str,
        )
        scene_entries = _decision_scene_entries(shared.scene_bus)

        for member_id in member_ids:
            if responded_members >= 2:
                break
            if not _should_teammate_respond(
                world,
                member_id,
                scene_entries=scene_entries,
                state=state,
                explicit_party=True,
            ):
                continue

            tm_prompt = await builder.build_teammate_interaction_prompt(
                member_id,
                group_mode=True,
                npc_id=None,
            )
            if tm_prompt is None:
                continue

            instance = (
                companion_manager.get_or_create(member_id, current_tick=current_tick)
                if companion_manager is not None
                else None
            )
            context_window = instance.context_window if instance is not None else None
            history = _window_to_history(context_window) if context_window is not None else None
            tm_layers = await builder.build_teammate_context(member_id)
            tm_context = builder.build_agent_context(
                "teammate",
                member_id,
                execute_command=execute_command,
            )
            try:
                agent_result = await self._executor.run_agentic(
                    role="teammate",
                    context=tm_context,
                    system_prompt=tm_prompt,
                    user_message=user_message,
                    max_turns=2,
                    context_layers=tm_layers,
                    conversation_history=history,
                )
            except Exception:
                logger.exception("Clue teammate Agent failed: %s", member_id)
                continue

            if _is_protocol_error(agent_result):
                _log_protocol_error(
                    role="teammate",
                    character_id=member_id,
                    result=agent_result,
                )
                continue

            teammate_events = _teammate_result_to_sse(member_id, agent_result)
            visible_reply = _extract_visible_reply_text(agent_result)
            if context_window is not None:
                context_window.add_message(
                    WindowMessage(
                        role="user",
                        content=user_message,
                        token_count=_approx_tokens(user_message),
                        metadata={},
                    )
                )
                if visible_reply:
                    context_window.add_message(
                        WindowMessage(
                            role="model",
                            content=visible_reply,
                            token_count=_approx_tokens(visible_reply),
                            metadata={},
                        )
                    )

            if any(event.event_type == "teammate_response" for event in teammate_events):
                responded_members += 1
                events.extend(teammate_events)
                scene_entries = _decision_scene_entries(shared.scene_bus) + [
                    {
                        "source": f"TEAMMATE:{member_id}",
                        "content": visible_reply,
                        "visibility": "public",
                        "tags": ["teammate_response", "clue_investigation"],
                    }
                ]

        return events

    def _build_fallback_clue_comment_event(
        self,
        clue_payload: Mapping[str, Any],
    ) -> SSEEvent:
        clue_name = str(clue_payload.get("clue_name") or clue_payload.get("clue_id") or "这条线索").strip()
        return SSEEvent(
            event_type="gm_comment",
            payload={
                "content": f"{clue_name}先把方向拧了出来，却还没打算把答案直接交到你手里。",
            },
        )

    def _build_clue_resolution_comment_event(
        self,
        result: PipelineResult,
    ) -> SSEEvent | None:
        metadata = result.metadata if isinstance(result.metadata, Mapping) else {}
        clue_name = str(metadata.get("clue_name") or metadata.get("clue_id") or "线索").strip()
        option_label = str(metadata.get("option_label") or metadata.get("option_id") or "这个判断").strip()
        passed = metadata.get("passed") if isinstance(metadata.get("passed"), bool) else None
        raw_effect_types = metadata.get("effect_types")
        effect_types = [
            str(item).strip()
            for item in raw_effect_types
            if isinstance(item, str) and str(item).strip()
        ] if isinstance(raw_effect_types, list) else []

        if "unlock_sub_location" in effect_types:
            content = f"{option_label}让{clue_name}终于露出了一条能追下去的路。"
        elif "advance_quest" in effect_types:
            content = f"{option_label}把{clue_name}钉进了更清楚的方向，事情往前走了一步。"
        elif passed is False:
            content = f"{option_label}没能把{clue_name}彻底掰开，但它至少替你排掉了一条岔路。"
        else:
            content = f"{option_label}暂时替{clue_name}定住了一个方向。"
        return SSEEvent(event_type="gm_comment", payload={"content": content})

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
            _result_decision_payload(result),
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
            _result_decision_payload(result),
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
                **_result_decision_payload(result),
                "npc_id": npc_id,
                "action_context": dict(action_context),
            },
            ensure_ascii=False,
            default=str,
        )
        # Inject NPC capability context into GM prompt so it can generate
        # functional options that match the NPC's actual abilities (2-2)
        npc_cap_context = _build_gm_npc_capability_context(
            shared.world, shared.state, npc_id
        )
        gm_system_prompt = builder.build_gm_dialogue_options_prompt() + npc_cap_context
        try:
            agent_result = await self._executor.run_agentic(
                role="gm",
                context=context,
                system_prompt=gm_system_prompt,
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
        companion_manager = getattr(session.runtime, "companion_manager", None)
        current_tick = state.time.absolute_tick() if state.has_slice("time") else 0
        member_ids = [member_id for member_id in members if str(member_id).strip()]
        if companion_manager is not None:
            companion_manager.sync_members(member_ids, current_tick=current_tick)

        user_message = json.dumps(
            _result_decision_payload(result),
            ensure_ascii=False,
            default=str,
        )

        # Create builder once — reused across all teammates in this tick
        builder = AgentContextBuilder(world, state)

        for member_id in member_ids:
            instance = (
                companion_manager.get_or_create(member_id, current_tick=current_tick)
                if companion_manager is not None
                else None
            )
            context_window = instance.context_window if instance is not None else None
            history = _window_to_history(context_window) if context_window is not None else None
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
                    conversation_history=history,
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

            if context_window is not None:
                context_window.add_message(
                    WindowMessage(
                        role="user",
                        content=user_message,
                        token_count=_approx_tokens(user_message),
                        metadata={},
                    )
                )
                visible_reply = _extract_visible_reply_text(agent_result)
                if visible_reply:
                    context_window.add_message(
                        WindowMessage(
                            role="model",
                            content=visible_reply,
                            token_count=_approx_tokens(visible_reply),
                            metadata={},
                        )
                    )

            teammate_events = _teammate_result_to_sse(member_id, agent_result)
            for event in teammate_events:
                if event.event_type == "teammate_response":
                    shared.scene_bus.add_entry({
                        "source": f"TEAMMATE:{member_id}",
                        "content": str(event.payload.get("content") or event.payload.get("action", "")),
                        "visibility": "public",
                        "tags": [event.event_type, "passive_reaction"],
                    })
            events.extend(teammate_events)

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
        companion_manager = shared.companion_manager
        current_tick = state.time.absolute_tick() if state.has_slice("time") else 0
        member_ids = [member_id for member_id in members if str(member_id).strip()]
        if companion_manager is not None:
            companion_manager.sync_members(member_ids, current_tick=current_tick)

        user_message = json.dumps(
            _result_decision_payload(result),
            ensure_ascii=False,
            default=str,
        )
        builder = AgentContextBuilder(world, state)
        scene_entries = _decision_scene_entries(shared.scene_bus)

        for member_id in member_ids:
            if not _should_teammate_respond(world, member_id, scene_entries=scene_entries):
                continue
            instance = (
                companion_manager.get_or_create(member_id, current_tick=current_tick)
                if companion_manager is not None
                else None
            )
            context_window = instance.context_window if instance is not None else None
            history = _window_to_history(context_window) if context_window is not None else None
            tm_full = await builder.build_teammate_full_context(
                member_id,
                memory_retriever=self._memory_retriever,
                companion_instance=instance,
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
                    conversation_history=history,
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

            if context_window is not None:
                context_window.add_message(
                    WindowMessage(
                        role="user",
                        content=user_message,
                        token_count=_approx_tokens(user_message),
                        metadata={},
                    )
                )
                visible_reply = _extract_visible_reply_text(agent_result)
                if visible_reply:
                    context_window.add_message(
                        WindowMessage(
                            role="model",
                            content=visible_reply,
                            token_count=_approx_tokens(visible_reply),
                            metadata={},
                        )
                    )

            teammate_events = _teammate_result_to_sse(member_id, agent_result)
            for event in teammate_events:
                if event.event_type == "teammate_response":
                    shared.scene_bus.add_entry({
                        "source": f"TEAMMATE:{member_id}",
                        "content": str(event.payload.get("content") or event.payload.get("action", "")),
                        "visibility": "public",
                        "tags": [event.event_type, "passive_reaction"],
                    })
            events.extend(teammate_events)
            scene_entries = _decision_scene_entries(shared.scene_bus)

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
        if result.action_type in _NPC_PASSIVE_SKIP_ACTIONS:
            return []
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
                "context": "passive_observation",
                "what_happened": (
                    result.narrative_hints[0]
                    if result.narrative_hints
                    else f"The player performed: {result.action_type}"
                ),
                **_result_decision_payload(result),
            },
            ensure_ascii=False,
            default=str,
        )
        scene_entries = _decision_scene_entries(shared.scene_bus)
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
                is_passive=True,
            )
            if npc_full is None:
                continue
            npc_profile = (
                world.characters.get(npc_id)
                if world.has_registry("characters")
                else None
            )
            npc_tags = list(_profile_get(npc_profile, "tags", [])) if npc_profile is not None else []
            metadata: dict[str, Any] = {"is_passive": True}
            if memory_writer is not None:
                metadata["memory_writer"] = memory_writer
            context = builder.build_agent_context(
                "npc",
                npc_id,
                execute_command=execute_command,
                metadata=metadata,
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
            if agent_result.metadata.get("finish_reason") == "pass_turn":
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

            npc_events = _npc_result_to_sse(npc_id, agent_result)
            for event in npc_events:
                if event.event_type in ("npc_response", "npc_emote"):
                    event.payload["passive"] = True
                    shared.scene_bus.add_entry({
                        "source": f"NPC:{npc_id}",
                        "content": str(event.payload.get("content") or event.payload.get("action", "")),
                        "visibility": "public",
                        "tags": [event.event_type, "passive_reaction"],
                    })
            events.extend(npc_events)
            scene_entries = _decision_scene_entries(shared.scene_bus)

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
        await self._flush_pending_writebacks_for_world(
            session.runtime.world,
            companion_manager=getattr(session.runtime, "companion_manager", None),
        )

    async def _flush_pending_writebacks_for_world(
        self,
        world: Any,
        companion_manager: Any | None = None,
    ) -> None:
        if self._instance_manager is None:
            return
        for pending in self._instance_manager.drain_pending_writebacks():
            await self._write_episode_for_world(
                world,
                pending.actor_id,
                pending.messages,
                companion_manager=companion_manager,
            )

    async def _write_episode(
        self,
        session: ManagedSession,
        actor_id: str,
        messages: list[WindowMessage],
    ) -> None:
        await self._write_episode_for_world(
            session.runtime.world,
            actor_id,
            messages,
            companion_manager=getattr(session.runtime, "companion_manager", None),
        )

    async def _write_episode_for_world(
        self,
        world: Any,
        actor_id: str,
        messages: list[WindowMessage],
        companion_manager: Any | None = None,
    ) -> None:
        if not messages:
            return
        graph = getattr(self._memory_retriever, "_graph", None)
        if graph is None:
            return
        companion = companion_manager.get(actor_id) if companion_manager is not None else None
        recent_events = []
        if companion is not None:
            recent_events = [
                {
                    "action": record.action_type,
                    "summary": record.summary,
                    "tags": record.tags,
                }
                for record in list(companion.get_recent_events(5))
            ]
        try:
            await graph.write_episode(
                actor_id=actor_id,
                messages=messages,
                context={"world": world, "recent_events": recent_events},
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
        if not current_area:
            return []
        current_location = state.player.current_location
        party_members: set[str] = set()
        if state.has_slice("party") and isinstance(state.party.members, dict):
            party_members = set(state.party.members.keys())
        area_npcs = get_area_npcs(state, shared.world, current_area)
        if focus_npc_id and focus_npc_id not in party_members:
            focus_location = area_npcs.get(focus_npc_id)
            if focus_location is not None and is_colocated(focus_location, current_location):
                return [focus_npc_id]
        nearby: list[str] = []
        for npc_id, location_id in area_npcs.items():
            if npc_id in party_members:
                continue
            if not is_colocated(location_id, current_location):
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


def _format_round_for_window(round_messages: list[RoundMessage]) -> str:
    """Format round messages into a readable string for ContextWindow storage."""
    lines: list[str] = []
    for msg in round_messages:
        prefix = f"[{msg.speaker_role}:{msg.speaker_id}]"
        if msg.event_type == "emote":
            lines.append(f"{prefix} *{msg.content}*")
        else:
            lines.append(f"{prefix} {msg.content}")
    return "\n".join(lines)


def _extract_member_speech(
    ordered_responses: list[tuple[str, AgentResult]],
    member_id: str,
) -> str:
    """Extract the concatenated speech/emote text of a specific member."""
    parts: list[str] = []
    for mid, result in ordered_responses:
        if mid != member_id:
            continue
        for tr in result.tool_results:
            if not tr.ok or not tr.message:
                continue
            evt = tr.metadata.get("event_type", "") if isinstance(tr.metadata, dict) else ""
            if evt in ("speech", "emote"):
                parts.append(tr.message)
    return " ".join(parts)


def _write_teammate_context_windows(
    session: ManagedSession,
    result: NpcInteractionResult,
) -> None:
    """Write the round record into each audience teammate's ContextWindow."""
    if not result.round_messages:
        return

    companion_mgr = getattr(session.runtime, "companion_manager", None)
    if companion_mgr is None:
        return

    # Deduplicate audience IDs while preserving order.
    seen: set[str] = set()
    recipient_ids: list[str] = []
    for member_id in result.audience_member_ids:
        if member_id in seen:
            continue
        seen.add(member_id)
        recipient_ids.append(member_id)
    for mid, _res in result.ordered_responses:
        if mid not in seen:
            seen.add(mid)
            recipient_ids.append(mid)

    if not recipient_ids:
        return

    round_text = _format_round_for_window(result.round_messages)

    for member_id in recipient_ids:
        instance = companion_mgr.get_or_create(member_id)

        # User message: the full round transcript is visible even if the
        # teammate chooses to stay silent.
        instance.context_window.add_message(WindowMessage(
            role="user",
            content=round_text,
            token_count=_approx_tokens(round_text),
        ))

        # Model message: this teammate's own speech (if any)
        own_speech = _extract_member_speech(result.ordered_responses, member_id)
        if own_speech:
            instance.context_window.add_message(WindowMessage(
                role="model",
                content=own_speech,
                token_count=_approx_tokens(own_speech),
            ))


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
        if result.executed and result.delta is not None:
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
            if str(entry.get("source", "")).startswith(f"NPC:{char_id}"):
                recent_speaks += 1
        if "COMBAT_END" in all_tags:
            tendency += 0.2
        if "CRISIS" in all_tags:
            tendency += 0.3
        if "TRIVIAL" in all_tags:
            tendency -= 0.15
        tendency -= recent_speaks * 0.15

    return random.random() < max(0.05, min(0.95, tendency))


def _decision_scene_entries(scene_bus: Any) -> list[dict[str, Any]]:
    """Internal decision view: public/system entries, excluding private secrets."""
    snapshot = scene_bus.snapshot() if scene_bus is not None else {}
    visible: list[dict[str, Any]] = []
    for raw_entry in snapshot.get("entries", []):
        if not isinstance(raw_entry, dict):
            continue
        if str(raw_entry.get("visibility", "public")) == "private":
            continue
        visible.append(dict(raw_entry))
    return visible


def _result_decision_payload(result: PipelineResult) -> dict[str, Any]:
    outcome = result.metadata.get("outcome")
    return {
        "action_type": result.action_type,
        "executed": result.executed,
        "outcome": dict(outcome) if isinstance(outcome, dict) else None,
        "narrative_hints": list(result.narrative_hints),
        "time_cost": result.time_cost,
    }


# ------------------------------------------------------------------
# Result → SSE converters
# ------------------------------------------------------------------


def _npc_result_to_sse(npc_id: str, result: AgentResult) -> list[SSEEvent]:
    """Convert NPC AgentResult tool_results into SSE events."""
    events: list[SSEEvent] = []

    for tr in result.tool_results:
        if not tr.ok:
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
        elif event_type == "companion_recruited":
            events.append(SSEEvent(
                event_type="companion_recruited",
                payload={
                    "npc_id": tr.metadata.get("npc_id", npc_id),
                    "reason": "recruited",
                    "party_members": tr.metadata.get("party_members", []),
                },
            ))
        # Command-based tools (update_feeling, remember, etc.) execute
        # via execute_command callback — no separate SSE needed.

    return events


def _gm_result_to_sse(result: AgentResult) -> list[SSEEvent]:
    """Convert GM AgentResult into SSE events."""
    events: list[SSEEvent] = []

    for tr in result.tool_results:
        if not tr.ok:
            continue
        event_type = tr.metadata.get("event_type", "")

        if event_type == "gm_narration" and tr.message:
            events.append(SSEEvent(
                event_type="gm_narration",
                payload={"content": tr.message},
            ))
        elif event_type == "gm_comment" and tr.message:
            payload = {"content": tr.message}
            tone = tr.metadata.get("tone")
            if isinstance(tone, str) and tone.strip():
                payload["tone"] = tone.strip()
            events.append(SSEEvent(
                event_type="gm_comment",
                payload=payload,
            ))
        # pass_turn produces no event

    return events


def _teammate_result_to_sse(
    member_id: str, result: AgentResult,
) -> list[SSEEvent]:
    """Convert Teammate AgentResult into SSE events."""
    events: list[SSEEvent] = []

    for tr in result.tool_results:
        if not tr.ok:
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
        elif event_type == "companion_dismissed":
            events.append(SSEEvent(
                event_type="companion_dismissed",
                payload={
                    "npc_id": tr.metadata.get("npc_id", member_id),
                    "reason": tr.metadata.get("reason", "voluntary"),
                    "party_members": tr.metadata.get("party_members", []),
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


def _extract_suggest_options_from_result(result: AgentResult | None) -> list[dict[str, Any]] | None:
    if result is None:
        return None
    for tool_result in result.tool_results:
        metadata = tool_result.metadata if isinstance(tool_result.metadata, dict) else {}
        if not tool_result.ok or metadata.get("tool") != "suggest_options":
            continue
        options = metadata.get("options")
        if isinstance(options, list) and options:
            return [dict(option) for option in options if isinstance(option, dict)]
    return None


def _dialogue_option_dispatch(
    npc_id: str,
    option: dict[str, Any],
    *,
    scope: str = "public",
    action_dispatcher: Any = None,
) -> dict[str, Any] | None:
    player_message = str(option.get("message") or option.get("text") or "").strip()
    check = option.get("check")
    if isinstance(check, dict):
        skill = str(check.get("skill") or "").strip()
        dc = check.get("dc")
        intent = str(option.get("intent") or "").strip() or "talk"
        if skill and isinstance(dc, int) and player_message:
            return {
                "kind": "interact",
                "payload": {
                    "scope": "private" if scope == "private" else "public",
                    "intent": (
                        "talk"
                        if scope == "private"
                        else intent if intent in {"talk", "greet", "ask", "chat"} else "talk"
                    ),
                    "target_kind": "npc",
                    "target_id": npc_id,
                    "message": player_message,
                    "check_skill": skill,
                    "check_dc": dc,
                },
            }

    action = str(option.get("action") or "").strip()
    if action in {"leave", "farewell"}:
        return {"kind": "local", "payload": {"action": "leave_dialogue"}}

    intent = str(option.get("intent") or "").strip()
    if intent == "farewell":
        return {"kind": "local", "payload": {"action": "leave_dialogue"}}
    if scope == "private" and intent == "browse":
        return None
    if scope == "private" and player_message:
        return {
            "kind": "interact",
            "payload": {
                "scope": "private",
                "intent": "talk",
                "target_kind": "npc",
                "target_id": npc_id,
                "message": player_message,
            },
        }
    if intent == "browse":
        return {
            "kind": "interact",
            "payload": {
                "intent": "browse",
                "target_kind": "npc",
                "target_id": npc_id,
            },
        }
    if intent in {"talk", "greet", "ask", "chat"} and player_message:
        return {
            "kind": "interact",
            "payload": {
                "scope": scope if scope in {"public", "private"} else "public",
                "intent": intent,
                "target_kind": "npc",
                "target_id": npc_id,
                "message": player_message,
            },
        }

    item_id = str(option.get("item_id") or "").strip()
    quest_id = str(option.get("quest_id") or "").strip()
    count = option.get("count")
    if scope != "private" and intent in {"buy", "sell", "inspect_item"} and item_id:
        payload: dict[str, Any] = {
            "intent": intent,
            "target_kind": "npc",
            "target_id": npc_id,
            "item_id": item_id,
        }
        if isinstance(count, int) and count > 0:
            payload["count"] = count
        return {"kind": "interact", "payload": payload}
    if (
        scope != "private"
        and intent in {"ask_quest", "ask_progress", "ask_location", "ask_requirements", "ask_reward"}
        and quest_id
    ):
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
    scope: str = "public",
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
            scope=scope,
            action_dispatcher=action_dispatcher,
        )
        if dispatch is not None:
            entry["dispatch"] = dispatch
        serialized.append(entry)
    return serialized


def _opening_option_dispatch(
    session: ManagedSession,
    option: dict[str, Any],
) -> dict[str, Any] | None:
    overview = build_location_overview(session)
    current_location_id = str(overview.get("location_id") or "").strip()
    implied_npc_id = _opening_option_npc_id(option, overview)
    starter_message = str(option.get("message") or "").strip() or "你好。"
    check = option.get("check")
    if isinstance(check, dict):
        skill = str(check.get("skill") or "").strip()
        dc = check.get("dc")
        if skill and isinstance(dc, int):
            if implied_npc_id is not None and starter_message:
                return {
                    "kind": "interact",
                    "payload": {
                        "scope": "public",
                        "intent": "talk",
                        "target_kind": "npc",
                        "target_id": implied_npc_id,
                        "message": starter_message,
                        "check_skill": skill,
                        "check_dc": dc,
                    },
                }
            context: dict[str, Any] = {
                "interaction_type": "opening_option",
            }
            return {
                "kind": "action",
                "payload": {
                    "action_type": "skill_check",
                    "params": {"skill": skill, "dc": dc},
                    "context": context,
                },
            }

    action = str(option.get("action") or "").strip()

    if action == "talk_first_npc":
        present_npcs = [
            item for item in overview.get("present_npcs", [])
            if isinstance(item, dict) and str(item.get("character_id", "")).strip()
        ]
        if present_npcs:
            npc_id = implied_npc_id or str(present_npcs[0].get("character_id", "")).strip()
            return {
                "kind": "interact",
                "payload": {
                    "scope": "public",
                    "intent": "talk",
                    "target_kind": "npc",
                    "target_id": npc_id,
                    "message": starter_message,
                },
            }

    if action == "enter_first_sub_location":
        sub_locations = [
            item for item in overview.get("sub_locations", [])
            if isinstance(item, dict)
            and bool(item.get("available", False))
            and str(item.get("id", "")).strip()
            and str(item.get("id", "")).strip() != current_location_id
        ]
        if sub_locations:
            loc_id = str(sub_locations[0].get("id", "")).strip()
            return {
                "kind": "navigate",
                "payload": {
                    "action": "enter_sub_location",
                    "location_id": loc_id,
                },
            }

    if action == "move_first_exit":
        exits = [
            item for item in overview.get("exits", [])
            if isinstance(item, dict)
            and not bool(item.get("blocked", False))
            and str(item.get("target_area_id", "")).strip()
        ]
        if exits:
            target_area_id = str(exits[0].get("target_area_id", "")).strip()
            return {
                "kind": "navigate",
                "payload": {
                    "action": "move_area",
                    "area_id": target_area_id,
                },
            }

    if action == "look_around":
        return {
            "kind": "input",
            "payload": {
                "text": str(option.get("message") or option.get("text") or "观察四周").strip() or "观察四周",
            },
        }

    return None


def _opening_option_npc_id(
    option: dict[str, Any],
    overview: dict[str, Any],
) -> str | None:
    explicit = str(option.get("npc_id") or "").strip()
    if explicit:
        return explicit
    present_npcs = [
        item for item in overview.get("present_npcs", [])
        if isinstance(item, dict) and str(item.get("character_id", "")).strip()
    ]
    if len(present_npcs) == 1:
        return str(present_npcs[0].get("character_id", "")).strip() or None
    return None


def _serialize_opening_options(
    session: ManagedSession,
    options: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for raw in options:
        if not isinstance(raw, dict):
            continue
        entry = dict(raw)
        dispatch = _opening_option_dispatch(session, entry)
        if dispatch is None:
            continue
        entry["dispatch"] = dispatch
        serialized.append(entry)
    return serialized


def _build_opening_dialogue_options_event(
    session: ManagedSession,
    result: AgentResult | None,
) -> SSEEvent | None:
    options = _extract_suggest_options_from_result(result)
    if options:
        serialized = _serialize_opening_options(session, options)
        if serialized:
            return SSEEvent(
                event_type="dialogue_options",
                payload={"options": serialized},
            )

    fallback = build_opening_dialogue_options(session)
    if not fallback:
        return None
    return SSEEvent(
        event_type="dialogue_options",
        payload={"options": fallback},
    )


def _build_opening_user_message(session: ManagedSession) -> str:
    overview = build_location_overview(session)
    present_npcs = [
        str(item.get("name") or item.get("character_id") or "").strip()
        for item in overview.get("present_npcs", [])
        if isinstance(item, dict)
        and str(item.get("name") or item.get("character_id") or "").strip()
    ]
    sub_locations = [
        str(item.get("name") or item.get("id") or "").strip()
        for item in overview.get("sub_locations", [])
        if isinstance(item, dict)
        and bool(item.get("available", False))
        and str(item.get("name") or item.get("id") or "").strip()
    ]
    exits = [
        str(item.get("name") or item.get("target_area_id") or "").strip()
        for item in overview.get("exits", [])
        if isinstance(item, dict)
        and not bool(item.get("blocked", False))
        and str(item.get("name") or item.get("target_area_id") or "").strip()
    ]
    dynamic_quests = session.runtime.state.quests.snapshot().get("dynamic_quests", {})
    opening_quest_title = ""
    if isinstance(dynamic_quests, dict):
        for quest in dynamic_quests.values():
            if not isinstance(quest, dict):
                continue
            status = str(quest.get("status", "")).strip().lower()
            if status in {"available", "accepted", "active", "in_progress"}:
                opening_quest_title = str(quest.get("title") or quest.get("summary") or "").strip()
                if opening_quest_title:
                    break
    # Build optional narrative context from current_target_milestone (D-P31 Phase 1c)
    opening_narrative: dict[str, Any] | None = None
    if session.runtime.state.has_slice("narrative_plan"):
        ms_id = session.runtime.state.narrative_plan.current_target_milestone
        if ms_id and session.runtime.world.has_registry("quests"):
            ms = session.runtime.world.quests.get_milestone(ms_id)
            if ms:
                opening_narrative = {
                    "milestone_title": ms.title,
                    "narrative_context": ms.narrative_context,
                    "key_elements": ms.key_elements,
                }
                if ms.chapter_id:
                    ch = session.runtime.world.quests.get_chapter(ms.chapter_id)
                    if ch:
                        opening_narrative["chapter_title"] = ch.title
                        opening_narrative["chapter_description"] = ch.description

    payload = {
        "current_area": session.runtime.state.player.current_area,
        "current_location": session.runtime.state.player.current_location,
        "present_npcs": present_npcs[:3],
        "sub_locations": sub_locations[:3],
        "exits": exits[:3],
        "opening_quest": opening_quest_title or None,
        "opening_narrative": opening_narrative,
    }
    return json.dumps(payload, ensure_ascii=False)


# ------------------------------------------------------------------
# GM NPC capability context helper
# ------------------------------------------------------------------


def _build_gm_npc_capability_context(
    world: Any,
    state: Any,
    focus_npc_id: str,
) -> str:
    """Build a capability summary string for GM prompt injection.

    Returns a section describing the focused NPC's static tools and dynamic
    capabilities, plus a brief list of nearby NPCs with their functional tags.
    Returns empty string if no capability data is available.
    """
    lines: list[str] = []

    # --- Focused NPC capabilities ---
    char_registry = world.has_registry("characters") if hasattr(world, "has_registry") else False
    npc_profile = world.characters.get(focus_npc_id) if char_registry else None
    npc_name = str(_profile_get(npc_profile, "name", focus_npc_id)) if npc_profile else focus_npc_id
    npc_tags: list[str] = list(_profile_get(npc_profile, "tags", [])) if npc_profile else []

    # Static tool availability derived from NPC tags
    static_tools: list[str] = []
    if "merchant" in npc_tags or "shop" in npc_tags:
        static_tools.append("trade_browse（可交易）")
    if "quest_giver" in npc_tags or "guild" in npc_tags:
        static_tools.append("offer_quest（发布任务）")

    # Dynamic capabilities from narrative plan
    dynamic_caps: list[dict] = []
    if hasattr(state, "has_slice") and state.has_slice("narrative_plan"):
        dynamic_caps = state.narrative_plan.get_capabilities(focus_npc_id)

    if static_tools or dynamic_caps:
        lines.append(f"\n## 当前 NPC 的能力（生成选项时必须参考）")
        lines.append(f"NPC: {focus_npc_id} ({npc_name})")
        if static_tools:
            lines.append("静态工具: " + "、".join(static_tools))
        if dynamic_caps:
            cap_parts = [
                f"{c.get('capability_id', '?')}（{c.get('instruction', '')})"
                + (f", functional={c['functional']}" if c.get("functional") else "")
                for c in dynamic_caps
            ]
            lines.append("动态能力: " + "、".join(cap_parts))
        lines.append("只推荐 NPC 真正能做到的功能选项。不要推荐 NPC 没有的能力（如交易、教学等）。")

    # --- Nearby NPC brief capabilities ---
    nearby_lines: list[str] = []
    if hasattr(state, "has_slice") and state.has_slice("areas") and state.has_slice("player"):
        area_id = state.player.current_area or ""
        if area_id and area_id in state.areas.areas:
            npc_locations = dict(state.areas.areas[area_id].npc_locations)
            for nearby_id in npc_locations:
                if nearby_id == focus_npc_id:
                    continue
                nearby_profile = world.characters.get(nearby_id) if char_registry else None
                if nearby_profile is None:
                    continue
                nearby_name = str(_profile_get(nearby_profile, "name", nearby_id))
                nearby_tags: list[str] = list(_profile_get(nearby_profile, "tags", []))
                nearby_caps: list[dict] = []
                if hasattr(state, "has_slice") and state.has_slice("narrative_plan"):
                    nearby_caps = state.narrative_plan.get_capabilities(nearby_id)
                nearby_functional_tags: list[str] = []
                if "merchant" in nearby_tags or "shop" in nearby_tags:
                    nearby_functional_tags.append("trade_browse")
                for cap in nearby_caps:
                    if cap.get("functional"):
                        nearby_functional_tags.append(cap["functional"])
                if nearby_functional_tags:
                    tag_str = ", ".join(sorted(set(nearby_functional_tags)))
                    nearby_lines.append(f"- {nearby_id} ({nearby_name}): {tag_str}")

    if nearby_lines:
        lines.append("\n## 附近其他 NPC")
        lines.extend(nearby_lines)

    return "\n".join(lines) if lines else ""


def _private_chat_result_to_sse(
    result: PrivateChatResult,
    *,
    action_dispatcher: Any = None,
    current_location: str | None = None,
) -> list[SSEEvent]:
    """Convert 4-step private chat result → ordered SSE events.

    Order: scene_change (optional) → NPC response → GM inner monologue
           (optional, introspective) → dialogue options.

    QF-1: Only emit transition:"fade" on the first entry into a private scene.
    Subsequent messages in the same private scene only emit a scene_change
    without the transition to avoid repeated black-screen animations.
    """
    events: list[SSEEvent] = []
    # Scene change (private sub-area created)
    # QF-1: skip transition:"fade" if player is already in a private scene
    already_in_private = (
        isinstance(current_location, str)
        and current_location.startswith("_private_")
    )
    if result.scene_id:
        scene_payload: dict[str, Any] = {
            "location_id": result.scene_id,
            "location_name": result.scene_name,
            "background": "private",
            "ambient_preset": None,
            "ambient_override": None,
        }
        if not already_in_private:
            scene_payload["transition"] = "fade"
        events.append(SSEEvent(
            event_type="scene_change",
            payload=scene_payload,
        ))
    if result.npc_result is not None:
        events.extend(_npc_result_to_sse(result.npc_id, result.npc_result))
    # GM inner monologue (player's inner voice, not third-party narration)
    if result.gm_result is not None:
        for tr in result.gm_result.tool_results:
            if tr.ok and tr.metadata.get("event_type") == "gm_comment" and tr.message:
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
                    scope="private",
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

    # Step 4: Teammate reactions (ordered)
    if result.ordered_responses:
        for member_id, tm_result in result.ordered_responses:
            events.extend(_teammate_result_to_sse(member_id, tm_result))
    else:
        # Fallback for compatibility
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
                    scope="public",
                    action_dispatcher=action_dispatcher,
                ),
            },
        ))

    return events


def _public_utterance_result_to_sse(result: NpcInteractionResult) -> list[SSEEvent]:
    """Convert a public untargeted utterance result into SSE events."""

    events: list[SSEEvent] = []
    if result.gm_result is not None:
        events.extend(_gm_result_to_sse(result.gm_result))
    if result.ordered_responses:
        for member_id, tm_result in result.ordered_responses:
            events.extend(_teammate_result_to_sse(member_id, tm_result))
    else:
        for member_id, tm_result in result.teammate_results.items():
            events.extend(_teammate_result_to_sse(member_id, tm_result))
    return events


def _free_chat_result_to_sse(result: NpcInteractionResult) -> list[SSEEvent]:
    """Convert a free-chat NpcInteractionResult into SSE events.

    Free chat has no NPC/GM steps — only teammate reactions.
    """
    events: list[SSEEvent] = []

    if result.ordered_responses:
        for member_id, tm_result in result.ordered_responses:
            events.extend(_teammate_result_to_sse(member_id, tm_result))
    else:
        for member_id, tm_result in result.teammate_results.items():
            events.extend(_teammate_result_to_sse(member_id, tm_result))

    return events


def _visible_teammate_reply_summaries(result: NpcInteractionResult) -> list[dict[str, str]]:
    visible_event_types = {"speech", "emote"}
    ordered_results = result.ordered_responses or list(result.teammate_results.items())
    replies: list[dict[str, str]] = []
    for member_id, agent_result in ordered_results:
        for tool_result in agent_result.tool_results:
            metadata = tool_result.metadata if isinstance(tool_result.metadata, dict) else {}
            event_type = str(metadata.get("event_type") or "")
            if (
                tool_result.ok
                and tool_result.message
                and event_type in visible_event_types
            ):
                replies.append({
                    "character_id": member_id,
                    "type": event_type,
                    "content": tool_result.message,
                })
    return replies


def _has_gm_comment_event(events: list[SSEEvent]) -> bool:
    return any(event.event_type == "gm_comment" for event in events)


def _insert_event_before(
    events: list[SSEEvent],
    event: SSEEvent,
    *,
    before_event_types: set[str],
) -> list[SSEEvent]:
    for index, existing in enumerate(events):
        if existing.event_type in before_event_types:
            return [*events[:index], event, *events[index:]]
    return [*events, event]


def _first_gm_comment_event(result: AgentResult | None) -> SSEEvent | None:
    if result is None:
        return None
    for tr in result.tool_results:
        metadata = tr.metadata if isinstance(tr.metadata, dict) else {}
        if tr.ok and metadata.get("event_type") == "gm_comment" and tr.message:
            payload = {"content": tr.message}
            tone = metadata.get("tone")
            if isinstance(tone, str) and tone.strip():
                payload["tone"] = tone.strip()
            return SSEEvent(event_type="gm_comment", payload=payload)
    return None


def _first_visible_gm_text(events: list[SSEEvent]) -> str:
    for event in events:
        if event.event_type not in {"gm_comment", "gm_narration"}:
            continue
        content = str(event.payload.get("content") or "").strip()
        if content:
            return content
    return ""
