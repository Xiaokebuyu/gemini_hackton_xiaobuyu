"""Narrative agent layer package."""

from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.executor import AgenticExecutor
from app.game_core.narrative.character_tools import (
    register_npc_tools,
    register_teammate_tools,
)
from app.game_core.narrative.gm_tools import register_gm_tools
from app.game_core.narrative.models import AgentResult, ToolResult
from app.game_core.narrative.registry import RoleToolRegistry
from app.game_core.narrative.tools import AgentTool

__all__ = [
    "AgentContext",
    "AgentResult",
    "AgentTool",
    "AgenticExecutor",
    "RoleToolRegistry",
    "ToolResult",
    "register_gm_tools",
    "register_npc_tools",
    "register_teammate_tools",
]
