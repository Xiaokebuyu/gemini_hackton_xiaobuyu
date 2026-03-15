"""NPC Interaction Coordinator — 6-step interaction pipeline.

Implements 编排层设计规范 §5 (NpcInteractionCoordinator).

6-step flow:
  1. Setup     — validate NPC, write player message to SceneBus, build context
  2. NPC Agent — AgenticExecutor runs NPC tools (speak/emote/update_feeling/…)
  3. GM        — observes conversation, defaults to pass_turn
  4. Teammate  — each party member probabilistically decides to react
  5. Options   — static dialogue options from NPC profile (LLM-enhanced: N-6)
  6. Return    — aggregate results, time_cost = 1/6

Decision record: D-I01 (interaction.md)
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Mapping

from app.game_core.content import WorldInstance
from app.game_core.narrative.companion_runtime import CompanionRuntimeManager
from app.game_core.narrative.context_builder import AgentContextBuilder, NpcFullContext, _extract_role_data, _profile_get
from app.game_core.narrative.context_window import ContextWindow, WindowMessage, window_to_history
from app.game_core.narrative.executor import AgenticExecutor
from app.game_core.narrative.instance_manager import NPCInstance
from app.game_core.narrative.memory_retriever import MemoryRetriever
from app.game_core.narrative.models import AgentResult
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer
from app.game_core.state.slices.scene import SceneEntry

logger = logging.getLogger(__name__)

@dataclass(slots=True)
class RoundMessage:
    """A single message in a multi-round group dialogue."""

    speaker_id: str
    speaker_role: str   # "player" | "npc" | "teammate"
    content: str
    event_type: str     # "speech" | "emote"


_DIALOGUE_BASE_DC: dict[str, int] = {
    "persuasion": 12,
    "deception": 13,
    "intimidation": 14,
    "performance": 12,
    "insight": 12,
    "perception": 12,
    "investigation": 13,
    "survival": 12,
    "nature": 12,
    "history": 12,
    "arcana": 13,
    "religion": 12,
    "athletics": 12,
    "acrobatics": 12,
    "stealth": 13,
}

_DIALOGUE_STAGE_DC_MOD: dict[str, int] = {
    "intimate": -3,
    "close_friend": -2,
    "friend": -1,
    "acquaintance": 0,
    "stranger": 1,
    "cold": 2,
    "hostile": 4,
    "enemy": 6,
}


def _extract_suggest_options(result: AgentResult | None) -> list[dict[str, Any]] | None:
    """Return validated suggest_options output from one GM result, if present."""
    if result is None:
        return None
    for tool_result in result.tool_results:
        metadata = tool_result.metadata if isinstance(tool_result.metadata, dict) else {}
        if not tool_result.ok or metadata.get("tool") != "suggest_options":
            continue
        options = metadata.get("options")
        if isinstance(options, list) and options:
            return options
    return None


def _resolve_dialogue_options(
    world: WorldInstance,
    state: StateContainer,
    npc_id: str,
    *,
    agent_result: AgentResult | None = None,
    options: list[dict[str, Any]] | None = None,
    allow_static_fallback: bool = True,
) -> list[dict[str, Any]]:
    """Normalize dialogue options, optionally falling back to static options."""
    resolved = options if options is not None else _extract_suggest_options(agent_result)
    if resolved is None and allow_static_fallback:
        resolved = _build_static_dialogue_options(world, state, npc_id)
    if not resolved:
        return []
    return _finalize_dialogue_options(world, state, npc_id, resolved)


# ------------------------------------------------------------------
# Result model
# ------------------------------------------------------------------


@dataclass(slots=True)
class NpcInteractionResult:
    """Raw output of the 6-step NPC interaction pipeline.

    Contains raw AgentResult objects — SSE conversion is the app layer's
    responsibility (AgentOrchestrationService._interaction_result_to_sse).
    """

    completed: bool
    npc_id: str
    npc_result: AgentResult | None = None
    gm_result: AgentResult | None = None
    teammate_results: dict[str, AgentResult] = field(default_factory=dict)
    ordered_responses: list[tuple[str, AgentResult]] = field(default_factory=list)
    audience_member_ids: list[str] = field(default_factory=list)
    round_messages: list[RoundMessage] = field(default_factory=list)
    dialogue_options: list[dict[str, Any]] = field(default_factory=list)
    time_cost: float = 0.0          # §3.1: talk = 1/6 格
    error: str | None = None
    error_reason: str | None = None
    graphize_candidates: list[WindowMessage] = field(default_factory=list)


# ------------------------------------------------------------------
# Coordinator
# ------------------------------------------------------------------


class NpcInteractionCoordinator:
    """Orchestrates the 6-step NPC interaction pipeline.

    Pure game_core — takes injected dependencies, does not touch
    ManagedSession or app-layer services.
    """

    def __init__(
        self,
        executor: AgenticExecutor,
        world: WorldInstance,
        state: StateContainer,
        *,
        memory_retriever: MemoryRetriever | None = None,
        memory_writer: Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any] | None]] | None = None,
        companion_manager: CompanionRuntimeManager | None = None,
    ) -> None:
        self._executor = executor
        self._world = world
        self._state = state
        self._memory_retriever = memory_retriever
        self._memory_writer = memory_writer
        self._companion_manager = companion_manager

    def _write_public_player_message(self, player_message: str) -> None:
        if not self._state.has_slice("scene"):
            return
        self._state.scene.add_entry(SceneEntry(
            source="player",
            content=player_message,
            visibility="public",
            tags=["speech"],
        ))

    def _write_skill_check_observation(self, check_result: Mapping[str, Any] | None) -> None:
        if not check_result or not self._state.has_slice("scene"):
            return
        passed = bool(check_result.get("passed", False))
        self._state.scene.add_entry(SceneEntry(
            source="ENGINE",
            content=(
                f"Player attempted {check_result.get('skill', 'unknown')} check "
                f"(DC {check_result.get('dc', '?')}): "
                f"{'Passed' if passed else 'Failed'} "
                f"(rolled {check_result.get('total', 0)})"
            ),
            visibility="public",
            tags=["SKILL_CHECK"],
        ))

    def _format_check_constraint(self, check_result: Mapping[str, Any]) -> str:
        """Generate structured behavioral constraint text from a skill check result."""
        skill = str(check_result.get("skill", "unknown"))
        dc = check_result.get("dc", "?")
        total = check_result.get("total", 0)
        passed = bool(check_result.get("passed", False))

        # Skill-specific phrasing
        _PASS_EFFECTS = {
            "persuasion": "让步、透露信息或改变态度",
            "intimidation": "表现出畏惧、退缩或妥协",
            "deception": "相信对方的话，不再怀疑",
        }
        _FAIL_EFFECTS = {
            "persuasion": "更加警觉、不耐烦或怀疑",
            "intimidation": "表现出愤怒或不屑",
            "deception": "识破谎言，更加不信任",
        }

        skill_lower = skill.lower()
        if passed:
            effect = _PASS_EFFECTS.get(skill_lower, "做出有利于对方的反应")
            return (
                f"\n\n## 当前检定结果（必须遵守）\n"
                f"玩家对你发起了【{skill}】检定（DC {dc}），掷出 {total}，**成功**。\n"
                f"你必须在本轮回复中体现被影响的效果：{effect}。\n"
                f"你可以表现得不情愿，但最终结果必须顺从检定结果。"
            )
        else:
            effect = _FAIL_EFFECTS.get(skill_lower, "维持原有立场不变")
            return (
                f"\n\n## 当前检定结果（必须遵守）\n"
                f"玩家对你发起了【{skill}】检定（DC {dc}），掷出 {total}，**失败**。\n"
                f"你不应被影响。你可以{effect}。\n"
                f"不得因为玩家的话术好就改变立场——骰子已经决定了结果。"
            )

    def _team_scene_entries(self) -> list[dict[str, Any]]:
        if not self._state.has_slice("scene"):
            return []
        snap = self._state.scene.snapshot()
        return [dict(entry) for entry in snap.get("entries", []) if isinstance(entry, dict)]

    async def _run_teammate_rounds(
        self,
        *,
        builder: AgentContextBuilder,
        execute_command: Callable[[Command], ExecuteResult],
        round_messages: list[RoundMessage],
        npc_id: str | None,
        utterance_scope: Literal["public", "party"] = "public",
    ) -> tuple[dict[str, AgentResult], list[tuple[str, AgentResult]], list[str]]:
        teammate_results: dict[str, AgentResult] = {}
        ordered_teammate_responses: list[tuple[str, AgentResult]] = []
        audience_member_ids: list[str] = []

        if not self._state.has_slice("party"):
            return teammate_results, ordered_teammate_responses, audience_member_ids

        members = self._state.party.members
        if not isinstance(members, dict) or not members:
            return teammate_results, ordered_teammate_responses, audience_member_ids

        scene_entries = self._team_scene_entries()
        reply_counts: dict[str, int] = {}
        audience_member_ids = list(members.keys())

        while True:
            anyone_spoke = False
            for member_id in audience_member_ids:
                if reply_counts.get(member_id, 0) >= MAX_REPLIES_PER_PARTICIPANT:
                    continue
                if not _should_teammate_respond(
                    self._world,
                    member_id,
                    scene_entries=scene_entries,
                    state=self._state,
                    explicit_party=utterance_scope == "party",
                ):
                    continue

                tm_prompt = await builder.build_teammate_interaction_prompt(
                    member_id,
                    group_mode=True,
                    npc_id=npc_id,
                )
                if tm_prompt is None:
                    continue
                tm_layers = await builder.build_teammate_context(member_id)
                tm_context = builder.build_agent_context(
                    "teammate",
                    member_id,
                    execute_command=execute_command,
                    metadata={
                        "memory_retriever": self._memory_retriever,
                        "world": self._world,
                    },
                )
                cumulative_msg = _build_group_observation(round_messages)

                tm_history: list[dict[str, Any]] | None = None
                if self._companion_manager is not None:
                    companion_inst = self._companion_manager.get_or_create(member_id)
                    tm_history = window_to_history(companion_inst.context_window)

                try:
                    tm_result = await self._executor.run_agentic(
                        role="teammate",
                        context=tm_context,
                        system_prompt=tm_prompt,
                        user_message=cumulative_msg,
                        conversation_history=tm_history,
                        max_turns=2,
                        context_layers=tm_layers,
                    )
                except Exception:
                    logger.exception(
                        "NpcInteractionCoordinator: Teammate failed: %s",
                        member_id,
                    )
                    continue

                ordered_teammate_responses.append((member_id, tm_result))
                teammate_results[member_id] = tm_result

                for tool_result in tm_result.tool_results:
                    if not tool_result.ok:
                        continue
                    event_type = (
                        tool_result.metadata.get("event_type", "")
                        if isinstance(tool_result.metadata, dict) else ""
                    )
                    if event_type == "speech" and tool_result.message:
                        round_messages.append(
                            RoundMessage(member_id, "teammate", tool_result.message, "speech"),
                        )
                        reply_counts[member_id] = reply_counts.get(member_id, 0) + 1
                        anyone_spoke = True
                    elif event_type == "emote" and tool_result.message:
                        round_messages.append(
                            RoundMessage(member_id, "teammate", tool_result.message, "emote"),
                        )

            if not anyone_spoke:
                break

        return teammate_results, ordered_teammate_responses, audience_member_ids

    async def execute_interaction(
        self,
        npc_id: str,
        player_message: str,
        execute_command: Callable[[Command], ExecuteResult],
        intent: str = "talk",
        instance: NPCInstance | None = None,
        text_chunk_sink: Callable[[str], Awaitable[None]] | None = None,
        check_result: dict[str, Any] | None = None,
    ) -> NpcInteractionResult:
        """Run the full 6-step NPC interaction flow.

        Args:
            npc_id: Target NPC identifier.
            player_message: The player's dialogue input.
            execute_command: Callback to execute state-changing commands.
            intent: Interaction intent (talk/greet/ask/…).

        Returns:
            NpcInteractionResult with raw agent results and options.
        """
        builder = AgentContextBuilder(self._world, self._state)
        context_window: ContextWindow | None = (
            instance.context_window if instance is not None else None
        )

        # ---- Step 1: Setup ----------------------------------------
        npc_full = await builder.build_npc_full_context(
            npc_id,
            memory_retriever=self._memory_retriever,
        )
        if npc_full is None:
            logger.warning("NpcInteractionCoordinator: NPC not found: %s", npc_id)
            return NpcInteractionResult(
                completed=False, npc_id=npc_id, error="npc_not_found",
            )
        system_prompt = npc_full.system_prompt
        npc_layers = npc_full.layers
        npc_profile = self._world.characters.get(npc_id) if self._world.has_registry("characters") else None
        npc_tags = list(_profile_get(npc_profile, "tags", [])) if npc_profile is not None else []

        # Record interaction in FlagSlice for EventEngine npc_talked conditions.
        if self._state.has_slice("flags"):
            self._state.flags.set(f"talked_to_{npc_id}", True)

        self._write_public_player_message(player_message)
        self._write_skill_check_observation(check_result)

        # Inject check constraint into NPC system prompt to enforce dice results
        if check_result:
            system_prompt += self._format_check_constraint(check_result)

        # Phase 3b: Inject planner-assigned pending_topic from NPC blackboard.
        # The NpcDirectorSubSystem writes pending_topic when a direct_npc directive
        # carries a topic/goal.  We read it here and append a dedicated section to the
        # system prompt so the NPC knows it should proactively raise that topic.
        # The key is cleared after the interaction completes successfully (see Step 6 below).
        _pending_topic: str | None = None
        if self._state.has_slice("relations"):
            _bb = self._state.relations.get_blackboard(npc_id)
            _raw_topic = _bb.get("pending_topic", "")
            if isinstance(_raw_topic, str) and _raw_topic.strip():
                _pending_topic = _raw_topic.strip()
        if _pending_topic is not None:
            system_prompt += _build_pending_topic_prompt(_pending_topic)

        # QF-5: Inject capability boundary so NPC knows what it can/cannot do
        # Also inject dynamic capabilities assigned by the narrative planner
        _ni_dynamic_caps: list[dict] = []
        if self._state.has_slice("narrative_plan"):
            _ni_dynamic_caps = self._state.narrative_plan.get_capabilities(npc_id)
        system_prompt += _build_capability_boundary_prompt(self._executor, npc_tags, capabilities=_ni_dynamic_caps)

        npc_metadata: dict[str, Any] = {
            "memory_retriever": self._memory_retriever,
            "world": self._world,
        }
        if self._memory_writer is not None:
            npc_metadata["memory_writer"] = self._memory_writer

        # Inject npc_tags so tools like AssignQuestTool can check trait constraints
        npc_metadata["npc_tags"] = npc_tags

        # Inject role_data for tool-level constraint validation (S5-04)
        role_data = _extract_role_data(npc_tags, npc_id, self._state, self._world)
        if role_data is not None:
            npc_metadata["role_data"] = role_data

        npc_context = builder.build_agent_context(
            "npc",
            npc_id,
            execute_command=execute_command,
            metadata=npc_metadata,
        )

        # ---- Step 2: NPC Agent Response ---------------------------
        npc_result: AgentResult | None = None
        history = window_to_history(context_window) if context_window is not None else None
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
            logger.exception("NpcInteractionCoordinator: NPC agent failed: %s", npc_id)
            return NpcInteractionResult(completed=False, npc_id=npc_id, error="agent_failed")

        if (
            npc_result is not None
            and npc_result.metadata.get("status") == "protocol_error"
        ):
            reason = str(npc_result.metadata.get("reason") or "unknown_protocol_error")
            logger.warning(
                "NpcInteractionCoordinator: invalid NPC agent response npc=%s reason=%s tool_calls=%s text_present=%s",
                npc_id,
                reason,
                npc_result.metadata.get("tool_call_names", []),
                npc_result.metadata.get("text_present", False),
            )
            return NpcInteractionResult(
                completed=False,
                npc_id=npc_id,
                npc_result=npc_result,
                error="invalid_agent_response",
                error_reason=reason,
            )

        npc_speech = _extract_visible_reply_text(npc_result) if npc_result else ""

        # Update ContextWindow with this exchange and detect overflow.
        graphize_candidates: list[WindowMessage] = []
        if context_window is not None:
            should1 = context_window.add_message(WindowMessage(
                role="user", content=player_message,
                token_count=_approx_tokens(player_message), metadata={},
            ))
            should2 = False
            if npc_speech:
                should2 = context_window.add_message(WindowMessage(
                    role="model", content=npc_speech,
                    token_count=_approx_tokens(npc_speech), metadata={},
                    parts=npc_result.last_model_parts if npc_result is not None else None,
                ))
            if should1 or should2:
                graphize_candidates = context_window.collect_for_graphize()

        # Build shared user_message for GM + Teammate (Steps 3-4)
        observation_msg = json.dumps(
            {
                "interaction_type": "npc_dialogue",
                "npc_id": npc_id,
                "npc_response": npc_speech,
                "player_message": player_message,
            },
            ensure_ascii=False,
        )

        # ---- Step 3: GM Observation (default PASS) ----------------
        gm_result: AgentResult | None = None
        try:
            gm_context = builder.build_agent_context("gm")
            gm_layers = builder.build_gm_context()
            gm_result = await self._executor.run_agentic(
                role="gm",
                context=gm_context,
                system_prompt=builder.build_gm_interaction_prompt(),
                user_message=observation_msg,
                max_turns=2,
                context_layers=gm_layers,
            )
        except Exception:
            logger.exception("NpcInteractionCoordinator: GM observation failed")

        # ---- Step 4: Teammate Reactions (serialized multi-round) ----
        round_messages: list[RoundMessage] = [
            RoundMessage("player", "player", player_message, "speech"),
        ]
        if npc_speech:
            round_messages.append(RoundMessage(npc_id, "npc", npc_speech, "speech"))
        teammate_results, ordered_teammate_responses, audience_member_ids = (
            await self._run_teammate_rounds(
                builder=builder,
                execute_command=execute_command,
                round_messages=round_messages,
                npc_id=npc_id,
                utterance_scope="public",
            )
        )

        # ---- Step 5: Dialogue Options (LLM if GM called suggest_options) -
        dialogue_options = _resolve_dialogue_options(
            self._world,
            self._state,
            npc_id,
            agent_result=gm_result,
            allow_static_fallback=True,
        )

        # ---- Step 6: Return ---------------------------------------
        # Phase 3b: Clear pending_topic from NPC blackboard now that the NPC has
        # had the opportunity to raise it.  We clear unconditionally on successful
        # completion so the topic is not repeated in every subsequent interaction.
        if _pending_topic is not None and self._state.has_slice("relations"):
            self._state.relations.update_blackboard(npc_id, {"pending_topic": ""})
            logger.debug(
                "NpcInteractionCoordinator: cleared pending_topic for %s after interaction",
                npc_id,
            )

        return NpcInteractionResult(
            completed=True,
            npc_id=npc_id,
            npc_result=npc_result,
            gm_result=gm_result,
            teammate_results=teammate_results,
            ordered_responses=ordered_teammate_responses,
            audience_member_ids=audience_member_ids,
            round_messages=round_messages,
            dialogue_options=dialogue_options,
            time_cost=1 / 6,
            graphize_candidates=graphize_candidates,
        )

    async def execute_free_chat(
        self,
        player_message: str,
        execute_command: Callable[[Command], ExecuteResult],
    ) -> NpcInteractionResult:
        """Run party free-chat — teammate evaluation only, no NPC/GM.

        Skips Steps 1-3 of the full pipeline and runs only:
          Step 4: Serialized multi-round teammate evaluation
          Step 5: Generic dialogue options (no NPC-specific options)

        Returns NpcInteractionResult with npc_id="" and no npc_result/gm_result.
        """
        builder = AgentContextBuilder(self._world, self._state)
        self._write_public_player_message(player_message)
        round_messages: list[RoundMessage] = [
            RoundMessage("player", "player", player_message, "speech"),
        ]
        teammate_results, ordered_teammate_responses, audience_member_ids = (
            await self._run_teammate_rounds(
                builder=builder,
                execute_command=execute_command,
                round_messages=round_messages,
                npc_id=None,
                utterance_scope="party",
            )
        )

        return NpcInteractionResult(
            completed=True,
            npc_id="",
            teammate_results=teammate_results,
            ordered_responses=ordered_teammate_responses,
            audience_member_ids=audience_member_ids,
            round_messages=round_messages,
            dialogue_options=[],
            time_cost=1 / 6,
        )

    async def execute_public_utterance(
        self,
        player_message: str,
        execute_command: Callable[[Command], ExecuteResult],
        *,
        intent: str = "talk",
        check_result: Mapping[str, Any] | None = None,
    ) -> NpcInteractionResult:
        """Run one public untargeted utterance: GM + teammate reactions only."""

        del intent  # Current public utterance behavior does not branch on intent yet.
        builder = AgentContextBuilder(self._world, self._state)
        self._write_public_player_message(player_message)
        self._write_skill_check_observation(check_result)

        gm_result: AgentResult | None = None
        try:
            gm_context = builder.build_agent_context("gm")
            gm_layers = builder.build_gm_context()
            observation_msg = json.dumps(
                {
                    "interaction_type": "public_utterance",
                    "player_message": player_message,
                },
                ensure_ascii=False,
            )
            gm_result = await self._executor.run_agentic(
                role="gm",
                context=gm_context,
                system_prompt=builder.build_gm_interaction_prompt(),
                user_message=observation_msg,
                max_turns=2,
                context_layers=gm_layers,
            )
        except Exception:
            logger.exception("NpcInteractionCoordinator: GM public utterance observation failed")

        round_messages: list[RoundMessage] = [
            RoundMessage("player", "player", player_message, "speech"),
        ]
        teammate_results, ordered_teammate_responses, audience_member_ids = (
            await self._run_teammate_rounds(
                builder=builder,
                execute_command=execute_command,
                round_messages=round_messages,
                npc_id=None,
                utterance_scope="public",
            )
        )

        return NpcInteractionResult(
            completed=True,
            npc_id="",
            gm_result=gm_result,
            teammate_results=teammate_results,
            ordered_responses=ordered_teammate_responses,
            audience_member_ids=audience_member_ids,
            round_messages=round_messages,
            dialogue_options=[],
            time_cost=1 / 6,
        )


# ------------------------------------------------------------------
# Pure helpers
# ------------------------------------------------------------------


MAX_REPLIES_PER_PARTICIPANT = 2


def _build_group_observation(round_messages: list[RoundMessage]) -> str:
    """Build cumulative observation for the next participant."""
    return json.dumps({
        "interaction_type": "group_dialogue",
        "messages": [
            {"speaker": m.speaker_id, "role": m.speaker_role,
             "content": m.content, "type": m.event_type}
            for m in round_messages
        ],
    }, ensure_ascii=False)


def _extract_speech_text(result: AgentResult) -> str:
    """Extract concatenated speech text from NPC AgentResult."""
    parts = [
        tr.message
        for tr in result.tool_results
        if tr.ok
        and tr.metadata.get("event_type") == "speech"
        and tr.message
    ]
    return " ".join(parts) if parts else "(NPC said nothing)"


def _extract_visible_reply_text(result: AgentResult) -> str:
    """Extract the visible NPC reply that should be remembered in ContextWindow."""
    parts = [
        tr.message
        for tr in result.tool_results
        if tr.ok
        and tr.metadata.get("event_type") in {"speech", "refuse", "emote"}
        and tr.message
    ]
    return " ".join(parts)


def _should_teammate_respond(
    world: WorldInstance,
    char_id: str,
    scene_entries: list[dict[str, Any]] | None = None,
    *,
    state: StateContainer | None = None,
    explicit_party: bool = False,
) -> bool:
    """Probabilistic gate for teammate reactions (设计规范 §10.3.2).

    Reads ``response_tendency`` from the character profile (default 0.3).
    Applies scene-context adjustments when scene_entries are provided.
    Clamps result to [0.05, 0.95].
    """
    profile = None
    if world.has_registry("characters"):
        profile = world.characters.get(char_id)
    tendency: float = 0.3
    if profile is not None:
        raw = _profile_get(profile, "response_tendency", 0.3)
        try:
            tendency = float(raw)
        except (TypeError, ValueError):
            tendency = 0.3

    if state is not None and state.has_slice("relations"):
        stage = state.relations.get_stage(char_id) or "stranger"
        disposition = state.relations.get_disposition(char_id)
        approval = 0
        trust = 0
        if isinstance(disposition, Mapping):
            try:
                approval = int(disposition.get("approval", 0))
            except (TypeError, ValueError):
                approval = 0
            try:
                trust = int(disposition.get("trust", 0))
            except (TypeError, ValueError):
                trust = 0

        tendency += _DIALOGUE_STAGE_RESPONSE_MOD.get(stage, 0.0)
        tendency += _bucketed_relationship_response_mod(approval, trust)
        if explicit_party:
            tendency += 0.15

    if scene_entries:
        all_tags: set[str] = set()
        recent_speaks = 0
        for e in scene_entries:
            all_tags.update(e.get("tags", []) if isinstance(e, dict) else [])
            if (
                isinstance(e, dict)
                and str(e.get("source", "")).startswith(f"TEAMMATE:{char_id}")
            ):
                recent_speaks += 1

        if "COMBAT_END" in all_tags:
            tendency += 0.3
        if "CRISIS" in all_tags:
            tendency += 0.4
        if "TRIVIAL" in all_tags:
            tendency -= 0.2
        tendency -= recent_speaks * 0.15

    return random.random() < max(0.05, min(0.95, tendency))


_DIALOGUE_STAGE_RESPONSE_MOD: dict[str, float] = {
    "enemy": -0.45,
    "hostile": -0.30,
    "cold": -0.15,
    "stranger": -0.05,
    "acquaintance": 0.0,
    "friend": 0.10,
    "close_friend": 0.18,
    "intimate": 0.25,
}


def _bucketed_relationship_response_mod(approval: int, trust: int) -> float:
    tendency = 0.0

    if approval >= 60:
        tendency += 0.10
    elif approval >= 20:
        tendency += 0.05
    elif approval <= -60:
        tendency -= 0.18
    elif approval <= -20:
        tendency -= 0.08

    if trust >= 60:
        tendency += 0.10
    elif trust >= 20:
        tendency += 0.05
    elif trust <= -60:
        tendency -= 0.15
    elif trust <= -20:
        tendency -= 0.05

    return tendency


# _window_to_history is provided by context_window.window_to_history


def _approx_tokens(text: str) -> int:
    """Approximate token count for a text string (4 chars ≈ 1 token)."""
    return max(1, len(text) // 4)


def _build_static_dialogue_options(
    world: WorldInstance,
    state: StateContainer,
    npc_id: str,
) -> list[dict[str, Any]]:
    """Build context-aware static dialogue options (DC stub, N-6).

    Mirrors the intent logic in build_talk_snapshot_payload().
    """
    options: list[dict[str, Any]] = [
        {"text": "继续交谈", "intent": "talk"},
        {"text": "告别", "intent": "farewell"},
    ]

    # Merchant: add browse
    has_shop = (
        state.has_slice("relations")
        and isinstance(state.relations.shop_states, Mapping)
        and npc_id in state.relations.shop_states
    )
    if has_shop:
        options.insert(1, {"text": "查看商品", "intent": "browse"})

    # Active quests: add ask_quest
    has_quests = (
        state.has_slice("quests")
        and isinstance(state.quests.dynamic_quests, Mapping)
        and bool(state.quests.dynamic_quests)
    )
    if has_quests:
        options.insert(1, {"text": "询问任务", "intent": "ask_quest"})

    return options


def _finalize_dialogue_options(
    world: WorldInstance,
    state: StateContainer,
    npc_id: str,
    options: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize dialogue options and fill missing check DCs.

    This keeps dialogue option mechanics in game_core instead of pushing
    frontend/app-layer consumers to infer missing fields.
    """
    del world

    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(options):
        if not isinstance(raw, Mapping):
            continue

        text = str(raw.get("text") or raw.get("label") or "").strip()
        if not text:
            continue

        entry: dict[str, Any] = {
            "id": raw.get("id") if raw.get("id") is not None else f"{npc_id}:{index}",
            "text": text,
        }

        label = raw.get("label")
        if isinstance(label, str) and label.strip():
            entry["label"] = label.strip()

        icon = raw.get("icon")
        if isinstance(icon, str) and icon.strip():
            entry["icon"] = icon.strip()

        intent = raw.get("intent")
        if isinstance(intent, str) and intent.strip():
            entry["intent"] = intent.strip()

        action = raw.get("action")
        if isinstance(action, str) and action.strip():
            entry["action"] = action.strip()

        for key in ("quest_id", "item_id"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                entry[key] = value.strip()

        npc_id = raw.get("npc_id")
        if isinstance(npc_id, str) and npc_id.strip():
            entry["npc_id"] = npc_id.strip()

        message = raw.get("message")
        if isinstance(message, str) and message.strip():
            entry["message"] = message.strip()

        count = raw.get("count")
        if isinstance(count, int) and count > 0:
            entry["count"] = count

        functional = raw.get("functional")
        if isinstance(functional, Mapping):
            func_type = str(functional.get("type") or "").strip()
            raw_params = functional.get("params", {})
            if func_type:
                entry["functional"] = {
                    "type": func_type,
                    "params": (
                        dict(raw_params)
                        if isinstance(raw_params, Mapping)
                        else {}
                    ),
                }

        check = raw.get("check")
        if isinstance(check, Mapping):
            skill = str(check.get("skill") or check.get("type") or "").strip()
            if skill:
                dc = check.get("dc")
                resolved_dc = dc if isinstance(dc, int) and dc >= 0 else _resolve_dialogue_check_dc(
                    state,
                    npc_id,
                    skill,
                )
                entry["check"] = {
                    "skill": skill,
                    "dc": resolved_dc,
                }

        normalized.append(entry)

    return normalized


def _build_capability_boundary_prompt(
    executor: AgenticExecutor,
    npc_tags: list[str],
    capabilities: list[dict] = (),
) -> str:
    """Build Chinese capability boundary section for NPC system prompt (QF-5).

    Generates a concise "你可以/你不可以" summary from the actual tool list so
    the NPC knows its own boundaries and can tell players what it cannot do.

    ``capabilities`` is an optional list of dynamic CapabilityDescriptor dicts
    (from NarrativePlanSlice.get_capabilities) to inject as special abilities.
    """
    registry = getattr(executor, "tool_registry", None)
    tools = registry.get_tools_for("npc", traits=npc_tags) if registry is not None else []
    tool_names = {t.name for t in tools}

    # Fixed base capabilities every NPC always has
    can_do: list[str] = ["说话", "表达情感", "记住信息", "回忆过去的对话"]

    # Tool → Chinese description mapping
    _TOOL_CAN_DO: dict[str, str] = {
        "offer_trade": "展示商品和交易",
        "sell_to_player": "向玩家出售商品（真实扣金币）",
        "execute_service": "执行神殿服务（治疗、祝福等）",
        "offer_quest": "发布和介绍任务",
        "reveal_secret": "分享隐秘信息",
        "join_party": "邀请加入队伍",
    }
    for tool_name, description in _TOOL_CAN_DO.items():
        if tool_name in tool_names:
            can_do.append(description)

    # Inject dynamic capability instructions into can_do
    for cap in capabilities:
        instruction = str(cap.get("instruction", "")).strip()
        if instruction:
            can_do.append(instruction)

    lines = [
        "\n## 你当前的能力",
        f"你可以：{'、'.join(can_do)}。",
    ]

    # Append special dynamic capabilities section if any
    if capabilities:
        lines.append("\n## 你的特殊能力（由公会/上级分配）")
        for cap in capabilities:
            cap_id = str(cap.get("capability_id", "")).strip()
            instruction = str(cap.get("instruction", "")).strip()
            if cap_id and instruction:
                lines.append(f"- **{cap_id}**: {instruction}")

    return "\n".join(lines)


def _build_pending_topic_prompt(topic: str) -> str:
    """Build a system prompt section for a planner-assigned pending_topic (Phase 3b).

    The NpcDirectorSubSystem writes ``pending_topic`` to the NPC blackboard when a
    ``direct_npc`` directive carries a topic or goal.  This section is injected into
    the NPC system prompt so the NPC knows it should proactively bring up that topic
    in the current conversation.

    The field is cleared by the coordinator after the interaction completes.
    """
    return (
        f"\n\n## 你有话想对玩家说\n"
        f"导演已安排你在这次对话中提起以下话题。请自然地将它融入对话中，"
        f"不要生硬地切换，但确保在这次对话中提到它。\n"
        f"话题：{topic}"
    )


def _resolve_dialogue_check_dc(
    state: StateContainer,
    npc_id: str,
    skill: str,
) -> int:
    """Fallback DC model for dialogue checks.

    We do not yet have scene-authored per-line difficulty, so this uses the
    current relation state as the mechanical modifier described in the design
    notes: approval/trust/stage shift a skill's base DC and clamp to [5, 25].
    """
    base = _DIALOGUE_BASE_DC.get(skill, 12)
    stage = "stranger"
    approval = 0
    trust = 0

    if state.has_slice("relations"):
        relation_stage = state.relations.get_stage(npc_id)
        if isinstance(relation_stage, str) and relation_stage.strip():
            stage = relation_stage.strip()
        disposition = state.relations.get_disposition(npc_id)
        if isinstance(disposition, Mapping):
            approval = int(disposition.get("approval", 0))
            trust = int(disposition.get("trust", 0))

    if approval >= 60:
        approval_mod = -2
    elif approval >= 20:
        approval_mod = -1
    elif approval <= -60:
        approval_mod = 3
    elif approval <= -20:
        approval_mod = 1
    else:
        approval_mod = 0

    if trust >= 50:
        trust_mod = -1
    elif trust <= -20:
        trust_mod = 1
    else:
        trust_mod = 0

    stage_mod = _DIALOGUE_STAGE_DC_MOD.get(stage, 0)
    return max(5, min(25, base + stage_mod + approval_mod + trust_mod))
