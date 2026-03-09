"""LocalDesignSkillProvider — filesystem-backed DesignSkillPort implementation.

Reads Markdown design-skill templates from
  data/{world_id}/planner_skills/{category}/{name}.md

Path traversal is defended by rejecting any segment that contains "..", "/",
or "\\" characters before constructing the file path.

Decision record: D-P20a (narrative.md)
"""

from __future__ import annotations

from pathlib import Path


_FORBIDDEN = frozenset(("..", "/", "\\"))


def _safe_segment(value: str) -> str | None:
    """Return *value* if it is a safe path segment, else None."""
    if not value:
        return None
    for forbidden in _FORBIDDEN:
        if forbidden in value:
            return None
    return value


class LocalDesignSkillProvider:
    """App-layer implementation of DesignSkillPort.

    Resolves templates from ``{base_dir}/{world_id}/planner_skills/{category}/{name}.md``.
    The *base_dir* defaults to the ``data/`` directory at the project root.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        # Resolve to an absolute path so cwd changes never matter
        if base_dir is not None:
            self._base_dir = base_dir.resolve()
        else:
            # __file__ is app/design_skill_provider.py → parent is app/ → parent is backend/
            self._base_dir = Path(__file__).resolve().parent.parent / "data"

    async def read_skill(
        self,
        world_id: str,
        category: str,
        name: str,
    ) -> str | None:
        """Read one template file; returns content or None if not found."""
        safe_world = _safe_segment(world_id)
        safe_category = _safe_segment(category)
        safe_name = _safe_segment(name)
        if safe_world is None or safe_category is None or safe_name is None:
            return None
        path = (
            self._base_dir
            / safe_world
            / "planner_skills"
            / safe_category
            / f"{safe_name}.md"
        )
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    async def list_skills(
        self,
        world_id: str,
        category: str | None = None,
    ) -> list[dict[str, str]]:
        """List available templates under *world_id*, optionally filtered by *category*."""
        safe_world = _safe_segment(world_id)
        if safe_world is None:
            return []
        skills_dir = self._base_dir / safe_world / "planner_skills"
        if not skills_dir.is_dir():
            return []

        results: list[dict[str, str]] = []
        for cat_dir in sorted(skills_dir.iterdir()):
            if not cat_dir.is_dir():
                continue
            if category is not None and cat_dir.name != category:
                continue
            for f in sorted(cat_dir.glob("*.md")):
                results.append({"category": cat_dir.name, "name": f.stem})
        return results
