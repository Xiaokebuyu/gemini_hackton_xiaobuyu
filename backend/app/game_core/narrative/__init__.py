"""Narrative agent layer package."""

from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.context_builder import AgentContextBuilder, NpcFullContext, TeammateFull
from app.game_core.narrative.context_window import ContextWindow, WindowMessage
from app.game_core.narrative.executor import AgenticExecutor
from app.game_core.narrative.character_tools import (
    register_npc_tools,
    register_teammate_tools,
)
from app.game_core.narrative.gm_tools import register_gm_tools
from app.game_core.narrative.memory_retriever import MemoryRetriever, NullMemoryRetriever
from app.game_core.narrative.models import AgentResult, ToolResult
from app.game_core.narrative.registry import RoleToolRegistry
from app.game_core.narrative.tools import AgentTool

__all__ = [
    "AgentContext",
    "AgentContextBuilder",
    "NpcFullContext",
    "TeammateFull",
    "AgentResult",
    "AgentTool",
    "AgenticExecutor",
    "ContextWindow",
    "MemoryRetriever",
    "NullMemoryRetriever",
    "RoleToolRegistry",
    "ToolResult",
    "WindowMessage",
    "register_gm_tools",
    "register_npc_tools",
    "register_teammate_tools",
]
