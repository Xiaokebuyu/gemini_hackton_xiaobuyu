"""GM agent tools — read-only / narration-only, zero Command construction."""

from __future__ import annotations

from typing import Any

from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.models import ToolResult
from app.game_core.narrative.registry import RoleToolRegistry
from app.game_core.narrative.tools import AgentTool


# ------------------------------------------------------------------
# Base
# ------------------------------------------------------------------


class _GmTool(AgentTool):
    """All GM tools share allowed_roles=["gm"]."""

    @property
    def allowed_roles(self) -> list[str]:
        return ["gm"]


# ------------------------------------------------------------------
# describe_environment
# ------------------------------------------------------------------


class DescribeEnvironmentTool(_GmTool):
    """Read world content + game state, assemble environment reference."""

    @property
    def name(self) -> str:
        return "describe_environment"

    @property
    def description(self) -> str:
        return (
            "Gather reference material about the current environment: "
            "area state, NPCs present, map content, and time."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        area_id = ""
        if context.state.has_slice("player"):
            area_id = context.state.player.current_area or ""
        if not area_id:
            return ToolResult(
                success=True,
                message="No current area.",
                metadata={"status": "no_area"},
            )

        # Area runtime state
        area_info: dict[str, Any] = {"area_id": area_id}
        if context.state.has_slice("areas"):
            areas_slice = context.state.areas
            if area_id in areas_slice.areas:
                a = areas_slice.areas[area_id]
                area_info.update({
                    "exploration": a.exploration,
                    "danger_level": a.danger_level,
                    "npc_locations": dict(a.npc_locations),
                    "discovered_items": sorted(a.discovered_items),
                    "properties": dict(a.properties),
                })

        # Static map content
        map_content = None
        if context.world.has_registry("maps"):
            map_content = context.world.maps.get(area_id)

        # NPC details
        npcs: list[dict[str, Any]] = []
        npc_ids = area_info.get("npc_locations", {})
        if npc_ids and context.world.has_registry("characters"):
            for npc_id in npc_ids:
                char_data = context.world.characters.get(npc_id)
                if char_data is not None:
                    npcs.append({"id": npc_id, "data": char_data})

        # Time
        time_info = None
        if context.state.has_slice("time"):
            t = context.state.time
            time_info = {"day": t.day, "slot": t.slot}

        reference = {
            "area": area_info,
            "map_content": map_content,
            "npcs_present": npcs,
            "time": time_info,
        }

        return ToolResult(
            success=True,
            message=f"Environment reference for {area_id}.",
            metadata={"status": "ok", "reference": reference},
        )


# ------------------------------------------------------------------
# narrate
# ------------------------------------------------------------------


class NarrateTool(_GmTool):
    """Output objective environment / scene narration text."""

    @property
    def name(self) -> str:
        return "narrate"

    @property
    def description(self) -> str:
        return "Output objective environment or scene narration."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Narration text."},
            },
            "required": ["text"],
        }

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        text = params.get("text", "")
        if not isinstance(text, str) or not text.strip():
            return ToolResult(
                success=False,
                message="text is required.",
                metadata={"status": "invalid_params"},
            )
        return ToolResult(
            success=True,
            message=text.strip(),
            metadata={"status": "ok", "event_type": "gm_narration"},
        )


# ------------------------------------------------------------------
# comment
# ------------------------------------------------------------------


class CommentTool(_GmTool):
    """Output sarcastic / witty commentary on player behavior."""

    @property
    def name(self) -> str:
        return "comment"

    @property
    def description(self) -> str:
        return "Output sarcastic commentary on what the player just did."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Commentary text."},
            },
            "required": ["text"],
        }

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        text = params.get("text", "")
        if not isinstance(text, str) or not text.strip():
            return ToolResult(
                success=False,
                message="text is required.",
                metadata={"status": "invalid_params"},
            )
        return ToolResult(
            success=True,
            message=text.strip(),
            metadata={"status": "ok", "event_type": "gm_comment"},
        )


# ------------------------------------------------------------------
# pass_turn
# ------------------------------------------------------------------


class PassTurnTool(_GmTool):
    """Indicate no narration or commentary this turn."""

    @property
    def name(self) -> str:
        return "pass_turn"

    @property
    def description(self) -> str:
        return "Skip narration and commentary this turn."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        return ToolResult(
            success=True,
            message="",
            metadata={"status": "ok", "event_type": "pass"},
        )


# ------------------------------------------------------------------
# suggest_options
# ------------------------------------------------------------------


class SuggestOptionsTool(_GmTool):
    """Generate structured dialogue options with skill-check intents."""

    @property
    def name(self) -> str:
        return "suggest_options"

    @property
    def description(self) -> str:
        return (
            "Generate player dialogue options. Each option has text and "
            "either a skill check intent or an action intent."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "options": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "check": {
                                "type": "object",
                                "properties": {
                                    "skill": {"type": "string"},
                                },
                            },
                            "action": {"type": "string"},
                        },
                        "required": ["text"],
                    },
                },
            },
            "required": ["options"],
        }

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        raw_options = params.get("options")
        if not isinstance(raw_options, list) or not raw_options:
            return ToolResult(
                success=False,
                message="options list is required.",
                metadata={"status": "invalid_params"},
            )

        validated: list[dict[str, Any]] = []
        for opt in raw_options:
            entry = self._validate_option(opt)
            if entry is not None:
                validated.append(entry)

        if not validated:
            return ToolResult(
                success=False,
                message="No valid options provided.",
                metadata={"status": "invalid_params"},
            )

        return ToolResult(
            success=True,
            message=f"{len(validated)} options generated.",
            metadata={
                "status": "ok",
                "event_type": "dialogue_options",
                "options": validated,
            },
        )

    @staticmethod
    def _validate_option(opt: Any) -> dict[str, Any] | None:
        if not isinstance(opt, dict):
            return None
        text = opt.get("text")
        if not isinstance(text, str) or not text.strip():
            return None

        entry: dict[str, Any] = {"text": text.strip()}

        check = opt.get("check")
        if isinstance(check, dict):
            skill = check.get("skill")
            if isinstance(skill, str) and skill.strip():
                entry["check"] = {"skill": skill.strip()}
                return entry

        action = opt.get("action")
        if isinstance(action, str) and action.strip():
            entry["action"] = action.strip()
            return entry

        # option has text but neither valid check nor action
        return None


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

GM_TOOLS: list[type[_GmTool]] = [
    DescribeEnvironmentTool,
    NarrateTool,
    CommentTool,
    PassTurnTool,
    SuggestOptionsTool,
]


def register_gm_tools(registry: RoleToolRegistry) -> None:
    """Instantiate and register all GM tools."""
    for tool_cls in GM_TOOLS:
        registry.register(tool_cls())
