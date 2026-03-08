"""NPC + Teammate agent tools — SceneBus writes + Command construction."""

from __future__ import annotations

from typing import Any

from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.models import ToolResult
from app.game_core.narrative.registry import RoleToolRegistry
from app.game_core.narrative.tools import AgentTool
from app.game_core.rules.models import Command
from app.game_core.state.slices.scene import SceneEntry

_DISPOSITION_DIMENSIONS = frozenset({"approval", "trust", "fear", "romance"})


# ------------------------------------------------------------------
# Base
# ------------------------------------------------------------------


class _CharacterTool(AgentTool):
    """Base for NPC / Teammate tools that require character_id."""

    @staticmethod
    def _get_character_id(context: AgentContext) -> str:
        cid = context.metadata.get("character_id", "")
        return cid if isinstance(cid, str) else ""

    @staticmethod
    def _require_text(params: dict[str, Any], key: str) -> str | None:
        val = params.get(key, "")
        if isinstance(val, str) and val.strip():
            return val.strip()
        return None

    def _add_scene_entry(
        self,
        context: AgentContext,
        source: str,
        content: str,
        *,
        tags: list[str] | None = None,
        visibility: str | None = None,
        audience: list[str] | None = None,
    ) -> None:
        """Write to SceneBus if available (controlled exception A)."""
        metadata = context.metadata if isinstance(context.metadata, dict) else {}
        resolved_visibility = visibility or str(
            metadata.get("scene_visibility", "public")
        )
        resolved_audience = audience
        if resolved_audience is None:
            raw_audience = metadata.get("scene_audience")
            if isinstance(raw_audience, list):
                resolved_audience = [str(item) for item in raw_audience]
        if context.state.has_slice("scene"):
            context.state.scene.add_entry(
                SceneEntry(
                    source=source,
                    content=content,
                    visibility=resolved_visibility,
                    audience=resolved_audience,
                    tags=tags or [],
                )
            )

    def _no_character_id(self) -> ToolResult:
        return ToolResult(
            ok=False,
            message="character_id required in context.metadata.",
            metadata={"status": "missing_character_id"},
        )


# ------------------------------------------------------------------
# Shared: speak (NPC + Teammate)
# ------------------------------------------------------------------


class SpeakTool(_CharacterTool):
    """Output dialogue text, written to SceneBus."""

    @property
    def name(self) -> str:
        return "speak"

    @property
    def description(self) -> str:
        return "Say something in character."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Dialogue text."},
            },
            "required": ["text"],
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["npc", "teammate"]

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        text = self._require_text(params, "text")
        if not text:
            return ToolResult(
                ok=False,
                message="text is required.",
                metadata={"status": "invalid_params"},
            )

        self._add_scene_entry(context, character_id, text, tags=["speech"])
        return ToolResult(
            ok=True,
            message=text,
            metadata={
                "status": "ok",
                "event_type": "speech",
                "character_id": character_id,
            },
        )


# ------------------------------------------------------------------
# Shared: emote (NPC + Teammate)
# ------------------------------------------------------------------


class EmoteTool(_CharacterTool):
    """Describe an emotion or physical action."""

    @property
    def name(self) -> str:
        return "emote"

    @property
    def description(self) -> str:
        return "Express an emotion or perform a physical action."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Action or emotion description.",
                },
            },
            "required": ["action"],
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["npc", "teammate"]

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        action = self._require_text(params, "action")
        if not action:
            return ToolResult(
                ok=False,
                message="action is required.",
                metadata={"status": "invalid_params"},
            )

        self._add_scene_entry(context, character_id, action, tags=["emote"])
        return ToolResult(
            ok=True,
            message=action,
            metadata={
                "status": "ok",
                "event_type": "emote",
                "character_id": character_id,
            },
        )


# ------------------------------------------------------------------
# NPC: update_feeling
# ------------------------------------------------------------------


class UpdateFeelingTool(_CharacterTool):
    """Modify a disposition dimension toward the player."""

    @property
    def name(self) -> str:
        return "update_feeling"

    @property
    def description(self) -> str:
        return "Change how this NPC feels about the player along one dimension."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "dimension": {
                    "type": "string",
                    "enum": sorted(_DISPOSITION_DIMENSIONS),
                    "description": "Feeling dimension to adjust.",
                },
                "delta": {
                    "type": "integer",
                    "description": "Amount to change (-50 to 50).",
                },
            },
            "required": ["dimension", "delta"],
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["npc"]

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        dimension = params.get("dimension", "")
        if not isinstance(dimension, str) or dimension not in _DISPOSITION_DIMENSIONS:
            return ToolResult(
                ok=False,
                message=f"dimension must be one of {sorted(_DISPOSITION_DIMENSIONS)}.",
                metadata={"status": "invalid_params"},
            )

        delta = params.get("delta", 0)
        if not isinstance(delta, (int, float)) or isinstance(delta, bool):
            return ToolResult(
                ok=False,
                message="delta must be an integer.",
                metadata={"status": "invalid_params"},
            )
        delta = int(delta)
        if not -50 <= delta <= 50:
            return ToolResult(
                ok=False,
                message="delta must be in [-50, 50].",
                metadata={"status": "invalid_params"},
            )

        command = Command(
            type="modify_disposition",
            params={"npc_id": character_id, "dimension": dimension, "delta": delta},
            source="ai_osiris",
        )
        result = context.run_command(command)
        if not result.executed:
            return ToolResult(
                ok=False,
                message=result.errors[0] if result.errors else "command failed",
                metadata={"status": "command_failed"},
            )
        return ToolResult(
            ok=True,
            message=f"{dimension} changed by {delta}.",
            commands=[command],
            metadata={"status": "ok", "dimension": dimension, "delta": delta},
        )


# ------------------------------------------------------------------
# NPC: remember
# ------------------------------------------------------------------


class RememberTool(_CharacterTool):
    """Remember a piece of information."""

    @property
    def name(self) -> str:
        return "remember"

    @property
    def description(self) -> str:
        return "Commit a fact or impression to memory."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "knowledge": {
                    "type": "string",
                    "description": "What to remember.",
                },
            },
            "required": ["knowledge"],
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["npc"]

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        knowledge = self._require_text(params, "knowledge")
        if not knowledge:
            return ToolResult(
                ok=False,
                message="knowledge is required.",
                metadata={"status": "invalid_params"},
            )

        writer = context.metadata.get("memory_writer")
        if not callable(writer):
            return ToolResult(
                ok=False,
                message="memory writer unavailable.",
                metadata={"status": "memory_unavailable"},
            )
        try:
            write_result = await writer(
                character_id,
                knowledge,
                {"scene_entries": list(context.scene_entries)},
            )
        except Exception:
            return ToolResult(
                ok=False,
                message="memory write failed.",
                metadata={"status": "memory_write_failed"},
            )
        metadata = {"status": "ok", "event_type": "memory_written"}
        if isinstance(write_result, dict):
            metadata.update({str(k): v for k, v in write_result.items()})
        return ToolResult(
            ok=True,
            message=f"Remembered: {knowledge}",
            metadata=metadata,
        )


# ------------------------------------------------------------------
# NPC: offer_quest
# ------------------------------------------------------------------


class OfferQuestTool(_CharacterTool):
    """Offer a quest to the player."""

    @property
    def name(self) -> str:
        return "offer_quest"

    @property
    def description(self) -> str:
        return "Offer a quest to the player, transitioning it to available."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "quest_id": {
                    "type": "string",
                    "description": "The quest to offer.",
                },
            },
            "required": ["quest_id"],
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["npc"]

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        quest_id = self._require_text(params, "quest_id")
        if not quest_id:
            return ToolResult(
                ok=False,
                message="quest_id is required.",
                metadata={"status": "invalid_params"},
            )

        to_state = params.get("to_state", "AVAILABLE")
        if not isinstance(to_state, str) or not to_state.strip():
            to_state = "AVAILABLE"

        command = Command(
            type="advance_quest",
            params={"quest_id": quest_id, "to_state": to_state},
            source="npc",
        )
        result = context.run_command(command)
        if not result.executed:
            return ToolResult(
                ok=False,
                message=result.errors[0] if result.errors else "command failed",
                metadata={"status": "command_failed"},
            )
        return ToolResult(
            ok=True,
            message=f"Quest {quest_id} offered.",
            commands=[command],
            metadata={"status": "ok", "quest_id": quest_id},
        )


# ------------------------------------------------------------------
# NPC: offer_trade
# ------------------------------------------------------------------


class OfferTradeTool(_CharacterTool):
    """Display merchandise for trade (read-only). Merchant NPCs only."""

    @property
    def applicable_traits(self) -> list[str]:
        return ["merchant"]

    @property
    def name(self) -> str:
        return "offer_trade"

    @property
    def description(self) -> str:
        return "Show available merchandise to the player."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    @property
    def allowed_roles(self) -> list[str]:
        return ["npc"]

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        shop_data: dict[str, Any] = {}
        if context.state.has_slice("relations"):
            shop_data = dict(
                context.state.relations.shop_states.get(character_id, {})
            )

        return ToolResult(
            ok=True,
            message="Merchandise displayed." if shop_data else "No merchandise available.",
            metadata={
                "status": "ok",
                "event_type": "offer_trade",
                "character_id": character_id,
                "shop": shop_data,
            },
        )


# ------------------------------------------------------------------
# NPC: refuse
# ------------------------------------------------------------------


class RefuseTool(_CharacterTool):
    """Refuse a request from the player."""

    @property
    def name(self) -> str:
        return "refuse"

    @property
    def description(self) -> str:
        return "Refuse a request, optionally explaining why."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why the request is refused.",
                },
            },
            "required": ["reason"],
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["npc"]

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        reason = self._require_text(params, "reason")
        if not reason:
            return ToolResult(
                ok=False,
                message="reason is required.",
                metadata={"status": "invalid_params"},
            )

        self._add_scene_entry(context, character_id, reason, tags=["refuse"])
        return ToolResult(
            ok=True,
            message=reason,
            metadata={
                "status": "ok",
                "event_type": "refuse",
                "character_id": character_id,
            },
        )


# ------------------------------------------------------------------
# NPC: reveal_secret
# ------------------------------------------------------------------


class RevealSecretTool(_CharacterTool):
    """Reveal secret information if trust threshold is met."""

    @property
    def name(self) -> str:
        return "reveal_secret"

    @property
    def description(self) -> str:
        return "Reveal a secret to the player if trust is high enough."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "secret": {
                    "type": "string",
                    "description": "The secret to reveal.",
                },
                "trust_required": {
                    "type": "integer",
                    "description": "Minimum trust needed (default 0).",
                },
            },
            "required": ["secret"],
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["npc"]

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        secret = self._require_text(params, "secret")
        if not secret:
            return ToolResult(
                ok=False,
                message="secret is required.",
                metadata={"status": "invalid_params"},
            )

        # Trust threshold check
        trust_required = params.get("trust_required", 0)
        if not isinstance(trust_required, (int, float)) or isinstance(
            trust_required, bool
        ):
            trust_required = 0
        trust_required = int(trust_required)

        if trust_required > 0 and context.state.has_slice("relations"):
            dispositions = context.state.relations.npc_dispositions.get(
                character_id, {}
            )
            current_trust = dispositions.get("trust", 0)
            if current_trust < trust_required:
                return ToolResult(
                    ok=False,
                    message="Trust is not high enough to reveal this secret.",
                    metadata={
                        "status": "trust_insufficient",
                        "current_trust": current_trust,
                        "required": trust_required,
                    },
                )

        # Write secret to SceneBus
        self._add_scene_entry(
            context, character_id, secret, tags=["secret", "reveal"],
        )

        # Record that this NPC revealed the secret
        command = Command(
            type="add_knowledge",
            params={
                "npc_id": character_id,
                "knowledge": f"Revealed: {secret}",
            },
            source=character_id,
        )
        result = context.run_command(command)

        return ToolResult(
            ok=True,
            message=secret,
            commands=[command] if result.executed else [],
            metadata={
                "status": "ok",
                "event_type": "secret_reveal",
                "character_id": character_id,
                "knowledge_recorded": result.executed,
            },
        )


# ------------------------------------------------------------------
# Teammate: express_opinion
# ------------------------------------------------------------------


class ExpressOpinionTool(_CharacterTool):
    """Express approval or disapproval of the current situation."""

    @property
    def name(self) -> str:
        return "express_opinion"

    @property
    def description(self) -> str:
        return "Express approval or disapproval, adjusting companion opinion."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "delta": {
                    "type": "integer",
                    "description": "Approval change (-50 to 50).",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this opinion is expressed.",
                },
            },
            "required": ["delta", "reason"],
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["teammate"]

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        delta = params.get("delta", 0)
        if not isinstance(delta, (int, float)) or isinstance(delta, bool):
            return ToolResult(
                ok=False,
                message="delta must be an integer.",
                metadata={"status": "invalid_params"},
            )
        delta = int(delta)
        if not -50 <= delta <= 50:
            return ToolResult(
                ok=False,
                message="delta must be in [-50, 50].",
                metadata={"status": "invalid_params"},
            )

        reason = self._require_text(params, "reason")
        if not reason:
            return ToolResult(
                ok=False,
                message="reason is required.",
                metadata={"status": "invalid_params"},
            )

        command = Command(
            type="modify_approval",
            params={"character_id": character_id, "delta": delta},
            source="ai_osiris",
        )
        result = context.run_command(command)
        if not result.executed:
            return ToolResult(
                ok=False,
                message=result.errors[0] if result.errors else "command failed",
                metadata={"status": "command_failed"},
            )
        return ToolResult(
            ok=True,
            message=f"Opinion expressed: {reason} (approval {'+' if delta >= 0 else ''}{delta}).",
            commands=[command],
            metadata={"status": "ok", "delta": delta, "reason": reason},
        )


# ------------------------------------------------------------------
# NPC: join_party
# ------------------------------------------------------------------


class JoinPartyTool(_CharacterTool):
    """Request to join the player's party. Recruitable NPCs only."""

    @property
    def name(self) -> str:
        return "join_party"

    @property
    def description(self) -> str:
        return "Join the player's party as a companion."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    @property
    def allowed_roles(self) -> list[str]:
        return ["npc"]

    @property
    def applicable_traits(self) -> list[str]:
        return ["recruitable"]

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        command = Command(
            type="recruit_companion",
            params={"npc_id": character_id},
            source="npc",
        )
        result = context.run_command(command)
        if not result.executed:
            return ToolResult(
                ok=False,
                message=result.errors[0] if result.errors else "command failed",
                metadata={
                    "status": (
                        result.errors[0]
                        if result.errors else "command_failed"
                    )
                },
            )
        metadata = dict(result.metadata)

        return ToolResult(
            ok=True,
            message=f"{character_id} joined the party.",
            commands=[command],
            metadata={
                "status": metadata.get("status", "ok"),
                "event_type": metadata.get("event_type", "companion_recruited"),
                "npc_id": metadata.get("npc_id", character_id),
                "reason": metadata.get("reason", "recruited"),
                "party_members": metadata.get("party_members", []),
            },
        )


# ------------------------------------------------------------------
# Teammate: leave_party
# ------------------------------------------------------------------


class LeavePartyTool(_CharacterTool):
    """Leave the player's party voluntarily."""

    @property
    def name(self) -> str:
        return "leave_party"

    @property
    def description(self) -> str:
        return "Leave the player's party."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why the companion is leaving.",
                },
            },
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["teammate"]

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        reason = self._require_text(params, "reason") or "voluntary"
        command = Command(
            type="dismiss_companion",
            params={"npc_id": character_id, "reason": reason},
            source="teammate",
        )
        result = context.run_command(command)
        if not result.executed:
            return ToolResult(
                ok=False,
                message=result.errors[0] if result.errors else "command failed",
                metadata={
                    "status": (
                        result.errors[0]
                        if result.errors else "command_failed"
                    )
                },
            )
        metadata = dict(result.metadata)

        return ToolResult(
            ok=True,
            message=f"{character_id} left the party: {reason}",
            commands=[command],
            metadata={
                "status": metadata.get("status", "ok"),
                "event_type": metadata.get("event_type", "companion_dismissed"),
                "npc_id": metadata.get("npc_id", character_id),
                "reason": metadata.get("reason", reason),
                "party_members": metadata.get("party_members", []),
            },
        )


# ------------------------------------------------------------------
# Teammate: suggest_tactic
# ------------------------------------------------------------------


class SuggestTacticTool(_CharacterTool):
    """Suggest a combat or exploration tactic."""

    @property
    def name(self) -> str:
        return "suggest_tactic"

    @property
    def description(self) -> str:
        return "Suggest a tactic for the current situation."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tactic": {
                    "type": "string",
                    "description": "Tactical suggestion.",
                },
            },
            "required": ["tactic"],
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["teammate"]

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        tactic = self._require_text(params, "tactic")
        if not tactic:
            return ToolResult(
                ok=False,
                message="tactic is required.",
                metadata={"status": "invalid_params"},
            )
        return ToolResult(
            ok=True,
            message=tactic,
            metadata={"status": "ok", "event_type": "suggest_tactic"},
        )


# ------------------------------------------------------------------
# Teammate: share_memory
# ------------------------------------------------------------------


class ShareMemoryTool(_CharacterTool):
    """Recall and share a memory."""

    @property
    def name(self) -> str:
        return "share_memory"

    @property
    def description(self) -> str:
        return "Recall a past experience and share it."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "What to remember about.",
                },
            },
            "required": ["topic"],
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["teammate"]

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        topic = self._require_text(params, "topic")
        if not topic:
            return ToolResult(
                ok=False,
                message="topic is required.",
                metadata={"status": "invalid_params"},
            )

        memories: list[str] = []
        if context.state.has_slice("relations"):
            memories = context.state.relations.get_impressions(character_id)

        return ToolResult(
            ok=True,
            message=f"Recalling memories about: {topic}",
            metadata={
                "status": "ok",
                "event_type": "share_memory",
                "character_id": character_id,
                "topic": topic,
                "memories": memories,
            },
        )


# ------------------------------------------------------------------
# Teammate: request_action
# ------------------------------------------------------------------


class RequestActionTool(_CharacterTool):
    """Request the player to take a specific action."""

    @property
    def name(self) -> str:
        return "request_action"

    @property
    def description(self) -> str:
        return "Ask the player to do something."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "What to ask the player to do.",
                },
            },
            "required": ["request"],
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["teammate"]

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        request = self._require_text(params, "request")
        if not request:
            return ToolResult(
                ok=False,
                message="request is required.",
                metadata={"status": "invalid_params"},
            )
        return ToolResult(
            ok=True,
            message=request,
            metadata={"status": "ok", "event_type": "request_action"},
        )


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

_NPC_TOOLS: list[type[_CharacterTool]] = [
    SpeakTool,
    EmoteTool,
    UpdateFeelingTool,
    RememberTool,
    OfferQuestTool,
    OfferTradeTool,
    RefuseTool,
    RevealSecretTool,
    JoinPartyTool,
]

_TEAMMATE_TOOLS: list[type[_CharacterTool]] = [
    SpeakTool,
    EmoteTool,
    ExpressOpinionTool,
    SuggestTacticTool,
    ShareMemoryTool,
    RequestActionTool,
    LeavePartyTool,
]


def register_npc_tools(registry: RoleToolRegistry) -> None:
    """Instantiate and register all NPC tools."""
    for tool_cls in _NPC_TOOLS:
        registry.register(tool_cls())


def register_teammate_tools(registry: RoleToolRegistry) -> None:
    """Instantiate and register all Teammate tools."""
    for tool_cls in _TEAMMATE_TOOLS:
        registry.register(tool_cls())
