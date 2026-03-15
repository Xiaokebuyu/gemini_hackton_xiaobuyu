"""Registry for agent tools."""

from __future__ import annotations

from app.game_core.narrative.tools import AgentTool


class RoleToolRegistry:
    """Registry keyed by role."""

    def __init__(self) -> None:
        self._tools: dict[str, dict[str, AgentTool]] = {
            "gm": {},
            "npc": {},
            "teammate": {},
        }

    def register(self, tool: AgentTool) -> None:
        for role in tool.allowed_roles:
            self._tools.setdefault(role, {})[tool.name] = tool

    def get_tools_for(self, role: str, traits: list[str] | None = None) -> list[AgentTool]:
        tools = list(self._tools.get(role, {}).values())
        if traits is None:
            return tools
        trait_set = set(traits)
        return [
            t for t in tools
            if not t.applicable_traits
            or all(r in trait_set for r in t.applicable_traits)
        ]
