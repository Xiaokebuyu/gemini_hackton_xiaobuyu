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

import json
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.game_core.content import WorldInstance
from app.game_core.narrative.context_builder import AgentContextBuilder, NpcFullContext, _profile_get
from app.game_core.narrative.context_window import ContextWindow, WindowMessage
from app.game_core.narrative.executor import AgenticExecutor
from app.game_core.narrative.instance_manager import NPCInstance
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
# Private chat scene templates (keyed by area tag, lowercase)
# ------------------------------------------------------------------

_PRIVATE_CHAT_SCENES: dict[str, list[dict[str, str]]] = {
    "tavern": [
        {"name": "酒馆二楼的小包间", "description": "一间昏暗的小房间，只有一盏油灯和两把椅子。楼下的喧嚣声被厚重的木门隔绝在外。"},
        {"name": "后门外的僻静角落", "description": "酒馆后门外的小巷，头顶的木质屋檐遮住了月光。"},
    ],
    "town": [
        {"name": "城墙上的角落", "description": "城墙的一处凹角，可以俯瞰整个小镇的灯火。风很大，但没有人会来这里。"},
    ],
    "forest": [
        {"name": "远离营地的老树下", "description": "一棵巨大的橡树，粗壮的根部形成了天然的座椅。篝火的光在这里只剩下微弱的橙色。"},
    ],
    "default": [
        {"name": "僻静处", "description": "一个远离人群的安静角落。"},
    ],
}


# ------------------------------------------------------------------
# Result model
# ------------------------------------------------------------------


@dataclass(slots=True)
class PrivateChatResult:
    """Raw output of the 4-step private chat pipeline.

    SSE conversion is the app layer's responsibility
    (AgentOrchestrationService._private_chat_result_to_sse).

    No teammate_results — private conversations are not observable by third
    parties (§7.4).  gm_result is the player's inner monologue (Phase 2b),
    distinct from third-party GM observation.
    """

    success: bool
    npc_id: str
    npc_result: AgentResult | None = None
    dialogue_options: list[dict[str, Any]] = field(default_factory=list)
    time_cost: float = 0.0          # §7.4: private chat = 1/6 格
    error: str | None = None
    graphize_candidates: list[WindowMessage] = field(default_factory=list)
    scene_id: str | None = None     # 私聊临时子地点 ID（Phase 2）
    scene_name: str = ""            # 子地点名称（供 SSE 使用）
    gm_result: AgentResult | None = None  # GM 内心旁白（Phase 2b）


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
        memory_writer: Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any] | None]] | None = None,
    ) -> None:
        self._executor = executor
        self._world = world
        self._state = state
        self._memory_retriever = memory_retriever
        self._memory_writer = memory_writer

    async def execute(
        self,
        npc_id: str,
        player_message: str,
        execute_command: Callable[[Command], ExecuteResult],
        *,
        instance: NPCInstance | None = None,
        text_chunk_sink: Callable[[str], Awaitable[None]] | None = None,
    ) -> PrivateChatResult:
        """Run the 4-step private chat flow.

        Args:
            npc_id: Target NPC identifier.
            player_message: The player's dialogue input.
            execute_command: Callback to execute state-changing commands.
            instance: Optional per-NPC runtime instance (injected
                by app layer via InstanceManager).

        Returns:
            PrivateChatResult with raw NPC agent result and dialogue options.
        """
        builder = AgentContextBuilder(self._world, self._state)
        context_window: ContextWindow | None = (
            instance.context_window if instance is not None else None
        )

        # ---- Step 1: Setup ----------------------------------------
        active_directive: dict[str, Any] | None = None
        if instance is not None and self._state.has_slice("time"):
            active_directive = instance.consume_directive(
                self._state.time.absolute_tick()
            )
        npc_full = await builder.build_npc_full_context(
            npc_id,
            memory_retriever=self._memory_retriever,
            is_private=True,
            active_directive=active_directive,
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

        # Create private scene sub-area
        area_id = self._state.player.current_area if self._state.has_slice("player") else ""
        scene_result = self._create_private_scene(npc_id, area_id)
        scene_id = scene_result[0] if scene_result else None
        scene_name = scene_result[1] if scene_result else ""

        # Record interaction in FlagSlice for EventEngine npc_talked conditions.
        if self._state.has_slice("flags"):
            self._state.flags.set(f"talked_to_{npc_id}", True)

        # Private: scene entry is only visible to player + this NPC
        private_audience = ["player", f"npc:{npc_id}"]
        if self._state.has_slice("scene"):
            self._state.scene.add_entry(SceneEntry(
                source="player",
                content=player_message,
                visibility="private",
                audience=private_audience,
                tags=["speech"],
            ))

        npc_context = builder.build_agent_context(
            "npc",
            npc_id,
            execute_command=execute_command,
            metadata=(
                {"memory_writer": self._memory_writer}
                if self._memory_writer is not None else None
            ),
            scene_visibility="private",
            scene_audience=private_audience,
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

        # ---- Step 2.5: GM 内心旁白（默认 pass_turn，仅在特殊时刻说话）------
        gm_result: AgentResult | None = None
        try:
            is_first = context_window is None or len(context_window.messages) == 0
            gm_context = builder.build_agent_context("gm")
            observation = json.dumps({
                "interaction_type": "private_chat",
                "npc_id": npc_id,
                "npc_response": npc_speech,
                "player_message": player_message,
                "is_first_private_chat": is_first,
            }, ensure_ascii=False)
            gm_result = await self._executor.run_agentic(
                role="gm",
                context=gm_context,
                system_prompt=builder.build_gm_private_chat_prompt(),
                user_message=observation,
                max_turns=1,
                context_layers=builder.build_gm_context(),
            )
        except Exception:
            logger.exception("PrivateChatCoordinator: GM introspection failed: %s", npc_id)

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
            scene_id=scene_id,
            scene_name=scene_name,
            gm_result=gm_result,
        )


    def _create_private_scene(self, npc_id: str, area_id: str) -> tuple[str, str] | None:
        """创建私聊临时子地点，返回 (sub_area_id, name) 或 None。

        幂等：先清除此 NPC 已有的旧私聊场景，再新建。
        """
        if not self._state.has_slice("areas") or not area_id:
            return None

        # 清除旧私聊场景
        area_snap = self._state.areas.snapshot()
        area_data = (area_snap.get("areas") or {}).get(area_id, {})
        for sa in list(area_data.get("temporary_sub_areas", [])):
            if (
                isinstance(sa, dict)
                and sa.get("source") == "private_chat"
                and npc_id in sa.get("resident_npcs", [])
            ):
                self._state.areas.remove_temporary_sub_area(area_id, str(sa.get("id", "")))

        # 匹配场景模板
        area_template = (
            self._world.maps.get(area_id) if self._world.has_registry("maps") else None
        )
        area_tags = (
            [str(t).lower() for t in getattr(area_template, "tags", [])]
            if area_template else []
        )
        scene_list = _PRIVATE_CHAT_SCENES["default"]
        for tag in area_tags:
            if tag in _PRIVATE_CHAT_SCENES:
                scene_list = _PRIVATE_CHAT_SCENES[tag]
                break

        template = random.choice(scene_list)
        tick = (
            self._state.time.absolute_tick()
            if self._state.has_slice("time") else 0
        )
        sub_area_id = f"_private_{npc_id}_{tick}"

        self._state.areas.add_temporary_sub_area(area_id, {
            "id": sub_area_id,
            "name": template["name"],
            "description": template["description"],
            "tags": ["PRIVATE_CHAT", "TEMPORARY"],
            "type": "private",
            "resident_npcs": [npc_id],
            "source": "private_chat",
            "expiry": -1,
        })
        return sub_area_id, template["name"]


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
