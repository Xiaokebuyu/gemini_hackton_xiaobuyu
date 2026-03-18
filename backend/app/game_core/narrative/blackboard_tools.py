"""Blackboard tools — NPC self-update and cross-NPC messaging.

UpdateBlackboardTool:
    NPC updates its own blackboard (thoughts, mood, attitude, new_observation)
    after a conversation to record inner state changes.

SendNpcMessageTool:
    NPC writes a message into another NPC's blackboard observations, enabling
    asynchronous cross-NPC communication (the recipient sees it next activation).
"""

from __future__ import annotations

import logging
from typing import Any

from app.game_core.narrative.character_tools import _CharacterTool
from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.models import ToolResult

logger = logging.getLogger(__name__)

# Maximum observations kept per blackboard before overflow (task 3 territory).
_OBS_HARD_CAP = 20


# ---------------------------------------------------------------------------
# UpdateBlackboardTool
# ---------------------------------------------------------------------------


class UpdateBlackboardTool(_CharacterTool):
    """NPC updates its own inner state on the blackboard.

    Intended to be called at the end of a conversation to record how the NPC
    felt and what it noticed.  Goals are intentionally excluded — those are
    managed by Planner directives.
    """

    @property
    def name(self) -> str:
        return "update_blackboard"

    @property
    def description(self) -> str:
        return (
            "更新自己的内心想法、情绪和对冒险者的看法。在对话结束前调用来记录自己的感受变化。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "thoughts": {
                    "type": "string",
                    "description": "当前的想法/思绪（可选）",
                },
                "mood": {
                    "type": "string",
                    "description": "当前情绪状态（可选）",
                },
                "attitude_towards_player": {
                    "type": "string",
                    "description": "对冒险者的看法和理由（可选）",
                },
                "new_observation": {
                    "type": "string",
                    "description": "值得记住的新观察（可选）",
                },
            },
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["npc", "teammate"]

    @property
    def applicable_traits(self) -> list[str]:
        return []

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        if not context.state.has_slice("relations"):
            return ToolResult(
                ok=False,
                message="relations slice not available.",
                metadata={"status": "no_relations_slice"},
            )

        # At least one field must be provided.
        thoughts = self._require_text(params, "thoughts")
        mood = self._require_text(params, "mood")
        attitude = self._require_text(params, "attitude_towards_player")
        new_observation = self._require_text(params, "new_observation")

        if not any([thoughts, mood, attitude, new_observation]):
            return ToolResult(
                ok=False,
                message="至少需要提供一个字段（thoughts/mood/attitude_towards_player/new_observation）。",
                metadata={"status": "no_fields_provided"},
            )

        # Read current blackboard.
        board = context.state.relations.get_blackboard(character_id)

        updates: dict[str, Any] = {}

        # Direct overwrite fields.
        if thoughts is not None:
            updates["thoughts"] = thoughts
        if mood is not None:
            updates["mood"] = mood
        if attitude is not None:
            updates["attitude_towards_player"] = attitude

        # Append new_observation to the observations list (capped at _OBS_HARD_CAP).
        if new_observation is not None:
            existing_obs: list[str] = []
            raw_obs = board.get("observations")
            if isinstance(raw_obs, list):
                existing_obs = [str(o) for o in raw_obs]
            existing_obs.append(new_observation)
            # Truncate to most recent entries; overflow handling (pending_graphize)
            # is performed by NpcAutonomyHook (task 3).
            updates["observations"] = existing_obs[-_OBS_HARD_CAP:]

        context.state.relations.update_blackboard(character_id, updates)

        logger.debug(
            "update_blackboard: %s updated fields=%s",
            character_id,
            list(updates.keys()),
        )

        return ToolResult(
            ok=True,
            message="黑板已更新。",
            metadata={
                "status": "ok",
                "event_type": "blackboard_updated",
                "character_id": character_id,
                "updated_fields": sorted(updates.keys()),
            },
        )


# ---------------------------------------------------------------------------
# SendNpcMessageTool
# ---------------------------------------------------------------------------


class SendNpcMessageTool(_CharacterTool):
    """NPC sends an asynchronous message to another NPC's blackboard.

    The target NPC will see the message in its observations on the next
    activation.  The sender also records an outgoing note in its own
    observations.
    """

    @property
    def name(self) -> str:
        return "send_npc_message"

    @property
    def description(self) -> str:
        return "向另一个NPC发送消息或请求。对方会在下次活动时看到。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "target_npc_id": {
                    "type": "string",
                    "description": "目标NPC的ID",
                },
                "message": {
                    "type": "string",
                    "description": "消息内容",
                },
            },
            "required": ["target_npc_id", "message"],
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["npc", "teammate"]

    @property
    def applicable_traits(self) -> list[str]:
        return []

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        if not context.state.has_slice("relations"):
            return ToolResult(
                ok=False,
                message="relations slice not available.",
                metadata={"status": "no_relations_slice"},
            )

        # Validate required params.
        target_npc_id = self._require_text(params, "target_npc_id")
        if not target_npc_id:
            return ToolResult(
                ok=False,
                message="target_npc_id 是必填字段。",
                metadata={"status": "missing_target_npc_id"},
            )

        message = self._require_text(params, "message")
        if not message:
            return ToolResult(
                ok=False,
                message="message 是必填字段。",
                metadata={"status": "missing_message"},
            )

        # Cannot send to self.
        if target_npc_id == character_id:
            return ToolResult(
                ok=False,
                message="不能向自己发送消息。",
                metadata={"status": "self_message_rejected"},
            )

        # Validate target exists in world characters registry.
        if context.world.has_registry("characters"):
            target_char = context.world.characters.get(target_npc_id)
            if target_char is None:
                return ToolResult(
                    ok=False,
                    message=f"目标NPC '{target_npc_id}' 不存在。",
                    metadata={
                        "status": "target_not_found",
                        "target_npc_id": target_npc_id,
                    },
                )
            target_name = getattr(target_char, "name", target_npc_id)
        else:
            # No character registry loaded — accept without validation.
            target_name = target_npc_id

        # Resolve sender name.
        sender_name = character_id
        if context.world.has_registry("characters"):
            sender_char = context.world.characters.get(character_id)
            if sender_char is not None:
                sender_name = getattr(sender_char, "name", character_id)

        # --- Write to target NPC blackboard ---
        target_board = context.state.relations.get_blackboard(target_npc_id)
        target_obs: list[str] = []
        raw_target_obs = target_board.get("observations")
        if isinstance(raw_target_obs, list):
            target_obs = [str(o) for o in raw_target_obs]
        target_obs.append(f"来自{sender_name}的消息：{message}")
        target_updates: dict[str, Any] = {}
        if len(target_obs) > _OBS_HARD_CAP:
            target_overflow = target_obs[:-_OBS_HARD_CAP]
            target_existing_pending = target_board.get("pending_graphize", [])
            if isinstance(target_existing_pending, list):
                target_existing_pending = list(target_existing_pending)
            else:
                target_existing_pending = []
            target_existing_pending.extend(target_overflow)
            target_updates["pending_graphize"] = target_existing_pending
            target_updates["observations"] = target_obs[-_OBS_HARD_CAP:]
        else:
            target_updates["observations"] = target_obs
        context.state.relations.update_blackboard(target_npc_id, target_updates)

        # --- Write outgoing note to sender blackboard ---
        sender_board = context.state.relations.get_blackboard(character_id)
        sender_obs: list[str] = []
        raw_sender_obs = sender_board.get("observations")
        if isinstance(raw_sender_obs, list):
            sender_obs = [str(o) for o in raw_sender_obs]
        sender_obs.append(f"向{target_name}发送了消息：{message}")
        sender_updates: dict[str, Any] = {}
        if len(sender_obs) > _OBS_HARD_CAP:
            sender_overflow = sender_obs[:-_OBS_HARD_CAP]
            sender_existing_pending = sender_board.get("pending_graphize", [])
            if isinstance(sender_existing_pending, list):
                sender_existing_pending = list(sender_existing_pending)
            else:
                sender_existing_pending = []
            sender_existing_pending.extend(sender_overflow)
            sender_updates["pending_graphize"] = sender_existing_pending
            sender_updates["observations"] = sender_obs[-_OBS_HARD_CAP:]
        else:
            sender_updates["observations"] = sender_obs
        context.state.relations.update_blackboard(character_id, sender_updates)

        logger.debug(
            "send_npc_message: %s → %s",
            character_id,
            target_npc_id,
        )

        return ToolResult(
            ok=True,
            message=f"消息已发送给{target_name}。",
            metadata={
                "status": "ok",
                "sender_id": character_id,
                "target_npc_id": target_npc_id,
            },
        )
