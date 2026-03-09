"""DesignSkillPort — planner design-skill file retrieval boundary.

game_core only sees this Protocol; the real file-system implementation
(LocalDesignSkillProvider) lives in app/.

Phase 1b: read_skill tool for AgenticNarrativePlanner multi-turn agent.

Decision record: D-P20a (narrative.md)
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DesignSkillPort(Protocol):
    """Design-skill retrieval boundary for the narrative planner agent.

    Implementors live in app/ (e.g. LocalDesignSkillProvider).
    game_core only depends on this Protocol.
    """

    async def read_skill(
        self,
        world_id: str,
        category: str,
        name: str,
    ) -> str | None:
        """Read the content of one design-skill template file.

        Returns the file content as a UTF-8 string, or None when the file
        does not exist.

        Args:
            world_id: World identifier (e.g. "default_world").
            category: Skill category directory name
                (e.g. "quests", "npcs", "encounters").
            name: Template name without extension (e.g. "hunt", "escort").
        """
        ...

    async def list_skills(
        self,
        world_id: str,
        category: str | None = None,
    ) -> list[dict[str, str]]:
        """List available design-skill templates.

        Returns a list of dicts, each with keys ``"category"`` and ``"name"``.
        When *category* is given, only entries for that category are returned.
        """
        ...


class NullDesignSkillPort:
    """Safe default: no IO, always returns None / empty list.

    Used in tests and when design-skill files have not been configured.
    """

    async def read_skill(
        self,
        world_id: str,
        category: str,
        name: str,
    ) -> str | None:
        return None

    async def list_skills(
        self,
        world_id: str,
        category: str | None = None,
    ) -> list[dict[str, str]]:
        return []
