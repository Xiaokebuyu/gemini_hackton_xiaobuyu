"""RoleRegistry — 按 (role, traits) 映射沉浸式工具集。

Usage::

    from app.world.immersive_tools import AgenticContext

    ctx = AgenticContext(session=runtime, agent_id="merchant_01", role="npc", scene_bus=bus)
    tools = RoleRegistry.get_tools(role="npc", traits={"merchant"}, ctx=ctx)
    # tools: List[Callable] — 可直接传给 Gemini SDK
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class RoleRegistry:
    """根据角色 + traits 返回已绑定的沉浸式工具列表。"""

    ROLE_TOOL_SETS: Dict[str, Set[str]] = {
        "gm": {"base", "gm"},
        "npc": {"base"},
        "teammate": {"base", "teammate"},
    }

    @classmethod
    def get_tools(
        cls,
        role: str,
        traits: Optional[Set[str]] = None,
        ctx: Any = None,
    ) -> List[Callable]:
        """返回该角色可用的工具列表（已绑定 AgenticContext）。

        Args:
            role: "gm" / "npc" / "teammate"
            traits: NPC 特质，如 {"merchant", "guard"}
            ctx: AgenticContext 实例

        Returns:
            List[Callable] — 每个 callable 的 __annotations__/__signature__
            只包含 LLM 可见参数，可直接传给 Gemini SDK。
        """
        from app.world.immersive_tools import bind_tool, get_tool_registry

        allowed_roles = cls.ROLE_TOOL_SETS.get(role, {"base"})
        traits = traits or set()

        tools: List[Callable] = []
        for tool_def in get_tool_registry():
            if not tool_def.roles & allowed_roles:
                continue
            if tool_def.traits and not (tool_def.traits & traits):
                continue
            tools.append(bind_tool(tool_def, ctx))

        logger.debug(
            "[RoleRegistry] role=%s traits=%s -> %d tools",
            role, traits, len(tools),
        )
        return tools
