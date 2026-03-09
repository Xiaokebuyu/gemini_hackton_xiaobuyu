"""Planner agent tools — read_design_skill / list_design_skills.

These tools are available exclusively to the "planner" role and allow the
AgenticNarrativePlanner to look up design-template Markdown files during its
multi-turn agentic loop.

The tools read from context.metadata["design_skill_port"] (a DesignSkillPort
protocol instance) and context.metadata["world_id"] (str).  Both are injected
by AgenticNarrativePlanner before calling AgenticExecutor.run_agentic().

Decision record: D-P20a (narrative.md)
"""

from __future__ import annotations

from typing import Any

from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.models import ToolResult
from app.game_core.narrative.registry import RoleToolRegistry
from app.game_core.narrative.tools import AgentTool


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class _PlannerTool(AgentTool):
    """Planner-style tools with configurable role bindings."""

    def __init__(self, *, roles: list[str] | None = None) -> None:
        self._roles = list(roles or ["planner"])

    @property
    def allowed_roles(self) -> list[str]:
        return list(self._roles)


# ---------------------------------------------------------------------------
# read_design_skill
# ---------------------------------------------------------------------------


class ReadDesignSkillTool(_PlannerTool):
    """Read a design-template file from the skill library."""

    @property
    def name(self) -> str:
        return "read_design_skill"

    @property
    def description(self) -> str:
        return (
            "Read a design template file to obtain authoring guidelines for "
            "quests, NPCs, encounters, environments, items, areas, or social "
            "events. Call list_design_skills first to discover available names."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": (
                        "Template category: quests / npcs / encounters / "
                        "environments / narrative / items / areas / social"
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Template name without the .md extension "
                        "(e.g. hunt, escort, boss, trader_npc)."
                    ),
                },
            },
            "required": ["category", "name"],
        }

    async def execute(
        self, params: dict[str, Any], context: AgentContext
    ) -> ToolResult:
        category = params.get("category", "")
        name = params.get("name", "")
        if not isinstance(category, str) or not category.strip():
            return ToolResult(
                ok=False,
                message="category is required.",
                metadata={"status": "invalid_params"},
            )
        if not isinstance(name, str) or not name.strip():
            return ToolResult(
                ok=False,
                message="name is required.",
                metadata={"status": "invalid_params"},
            )

        port = (context.metadata or {}).get("design_skill_port")
        if port is None:
            return ToolResult(
                ok=False,
                message="Design skill port not configured.",
                metadata={"status": "no_port"},
            )

        world_id = str((context.metadata or {}).get("world_id", ""))
        allowed_categories = (context.metadata or {}).get("allowed_skill_categories")
        if (
            isinstance(allowed_categories, list)
            and allowed_categories
            and category.strip() not in {
                str(item).strip()
                for item in allowed_categories
                if isinstance(item, str) and str(item).strip()
            }
        ):
            return ToolResult(
                ok=False,
                message=f"Category not allowed for this role: {category.strip()}",
                metadata={"status": "forbidden_category"},
            )
        try:
            content = await port.read_skill(world_id, category.strip(), name.strip())
        except Exception as exc:
            return ToolResult(
                ok=False,
                message=f"Failed to read skill: {exc}",
                metadata={"status": "io_error"},
            )

        if content is None:
            return ToolResult(
                ok=False,
                message=f"Skill not found: {category}/{name}",
                metadata={"status": "not_found", "category": category, "name": name},
            )

        return ToolResult(
            ok=True,
            message=content,
            metadata={
                "status": "ok",
                "category": category.strip(),
                "name": name.strip(),
                "length": len(content),
            },
        )


# ---------------------------------------------------------------------------
# list_design_skills
# ---------------------------------------------------------------------------


class ListDesignSkillsTool(_PlannerTool):
    """List available design-template files in the skill library."""

    @property
    def name(self) -> str:
        return "list_design_skills"

    @property
    def description(self) -> str:
        return (
            "List available design template files. Use this to discover what "
            "templates exist before calling read_design_skill."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": (
                        "Optional: filter by category. "
                        "Omit to list all categories."
                    ),
                },
            },
            "required": [],
        }

    async def execute(
        self, params: dict[str, Any], context: AgentContext
    ) -> ToolResult:
        category = params.get("category")
        if category is not None and not isinstance(category, str):
            return ToolResult(
                ok=False,
                message="category must be a string.",
                metadata={"status": "invalid_params"},
            )

        port = (context.metadata or {}).get("design_skill_port")
        if port is None:
            return ToolResult(
                ok=False,
                message="Design skill port not configured.",
                metadata={"status": "no_port"},
            )

        world_id = str((context.metadata or {}).get("world_id", ""))
        allowed_categories = (context.metadata or {}).get("allowed_skill_categories")
        normalized_allowed: set[str] = set()
        if isinstance(allowed_categories, list):
            normalized_allowed = {
                str(item).strip()
                for item in allowed_categories
                if isinstance(item, str) and str(item).strip()
            }
        try:
            skills = await port.list_skills(
                world_id,
                category=category.strip() if isinstance(category, str) else None,
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                message=f"Failed to list skills: {exc}",
                metadata={"status": "io_error"},
            )

        if normalized_allowed:
            skills = [
                item
                for item in skills
                if isinstance(item, dict)
                and str(item.get("category", "")).strip() in normalized_allowed
            ]

        return ToolResult(
            ok=True,
            message=f"{len(skills)} skill(s) available.",
            metadata={
                "status": "ok",
                "skills": skills,
                "count": len(skills),
            },
        )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_PLANNER_TOOLS: list[type[_PlannerTool]] = [
    ReadDesignSkillTool,
    ListDesignSkillsTool,
]


def register_planner_tools(
    registry: RoleToolRegistry,
    *,
    roles: list[str] | None = None,
) -> None:
    """Instantiate and register all planner tools into *registry*."""
    for tool_cls in _PLANNER_TOOLS:
        registry.register(tool_cls(roles=roles))
