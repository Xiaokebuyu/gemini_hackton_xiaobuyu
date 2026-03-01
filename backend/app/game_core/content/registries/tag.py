"""TagRegistry implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry


@dataclass(slots=True)
class TagDimension:
    """Typed tag dimension definition."""

    id: str
    description: str = ""
    tags: list[str] = field(default_factory=list)


class TagRegistry(ContentRegistry):
    """Registry for tag definitions and dimensions."""

    def __init__(self) -> None:
        super().__init__("tags")
        self._dimensions: dict[str, TagDimension] = {}
        self._load_issues: list[str] = []

    def load(self, data: dict[str, Any]) -> None:
        self._dimensions = {}
        self._load_issues = []
        if not isinstance(data, Mapping):
            return
        for dimension, payload in data.items():
            if not isinstance(payload, Mapping):
                continue
            dim_key = str(dimension)
            dim_id = str(payload.get("id", dimension))

            raw_desc = payload.get("description")
            if raw_desc is not None and self._coerce_non_empty_string(raw_desc) is None:
                self._load_issues.append(
                    f"tag dimension '{dim_key}' has invalid description"
                )

            raw_tags = payload.get("tags")
            if raw_tags is not None:
                if not isinstance(raw_tags, list):
                    self._load_issues.append(
                        f"tag dimension '{dim_key}' has invalid tags"
                    )
                else:
                    for index, tag in enumerate(raw_tags):
                        if self._coerce_non_empty_string(tag) is None:
                            self._load_issues.append(
                                f"tag dimension '{dim_key}' tags[{index}] must be a non-empty string"
                            )

            self._dimensions[dim_key] = TagDimension(
                id=dim_id,
                description=str(payload.get("description") or ""),
                tags=(
                    [str(t) for t in raw_tags if isinstance(t, str) and str(t).strip()]
                    if isinstance(raw_tags, list) else []
                ),
            )

    def get(self, content_id: str) -> TagDimension | None:
        return self._dimensions.get(content_id)

    def list_all(self) -> list[TagDimension]:
        return list(self._dimensions.values())

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def all_tags(self) -> set[str]:
        """Return all tag values across all dimensions."""
        result: set[str] = set()
        for dim in self._dimensions.values():
            result.update(dim.tags)
        return result

    def get_dimension_for_tag(self, tag: str) -> str | None:
        """Return the dimension name that contains the given tag, or None."""
        for dim_key, dim in self._dimensions.items():
            if tag in dim.tags:
                return dim_key
        return None

    def has_tag(self, tag: str) -> bool:
        for dim in self._dimensions.values():
            if tag in dim.tags:
                return True
        return False

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        issues = list(self._load_issues)
        for key, dim in self._dimensions.items():
            if not dim.id:
                issues.append(f"tag dimension '{key}' missing id")
        return issues
