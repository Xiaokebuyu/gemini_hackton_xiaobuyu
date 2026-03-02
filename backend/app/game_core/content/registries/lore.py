"""LoreRegistry implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.game_core.content.base import ContentRegistry


@dataclass(slots=True)
class LoreEntry:
    """Typed lore / world-rule text block.

    TODO: 设计规范包含 scope 字段（global/regional/local）和 WorldRule 子类型，
          用于 AI Osiris 按范围过滤世界规则。当前 lore 条目数量有限，
          tags 过滤已足够；条目规模增长时按设计规范添加 scope 字段。
    """

    id: str
    title: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)


class LoreRegistry(ContentRegistry):
    """Registry for lore and world-rule text blocks."""

    def __init__(self) -> None:
        super().__init__("lore")
        self._items: dict[str, LoreEntry] = {}
        self._load_issues: list[str] = []

    def load(self, data: dict[str, Any]) -> None:
        self._items = {}
        self._load_issues = []
        coerced = self._coerce_dict_mapping(data)
        for lid, raw in coerced.items():
            for field_name in ("name", "title"):
                raw_val = raw.get(field_name)
                if raw_val is not None and self._coerce_non_empty_string(raw_val) is None:
                    self._load_issues.append(f"lore entry '{lid}' has invalid {field_name}")

            for field_name in ("content", "text"):
                raw_val = raw.get(field_name)
                if raw_val is not None and self._coerce_non_empty_string(raw_val) is None:
                    self._load_issues.append(f"lore entry '{lid}' has invalid {field_name}")

            raw_tags = raw.get("tags")
            if raw_tags is not None:
                if not isinstance(raw_tags, list):
                    self._load_issues.append(f"lore entry '{lid}' has invalid tags")
                else:
                    for index, tag in enumerate(raw_tags):
                        if self._coerce_non_empty_string(tag) is None:
                            self._load_issues.append(
                                f"lore entry '{lid}' tags[{index}] must be a non-empty string"
                            )

            self._items[lid] = LoreEntry(
                id=str(raw.get("id", lid)),
                title=str(raw.get("name") or raw.get("title") or ""),
                content=str(raw.get("content") or raw.get("text") or ""),
                tags=(
                    [str(t) for t in raw_tags if isinstance(t, str) and str(t).strip()]
                    if isinstance(raw_tags, list) else []
                ),
            )

    def get(self, content_id: str) -> LoreEntry | None:
        return self._items.get(content_id)

    def list_all(self) -> list[LoreEntry]:
        return list(self._items.values())

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_by_tag(self, tag: str) -> list[LoreEntry]:
        """Return lore entries that have the given tag in their tags list."""
        return [item for item in self._items.values() if tag in item.tags]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        issues = list(self._load_issues)
        for lid, entry in self._items.items():
            if not entry.id:
                issues.append(f"lore entry '{lid}' missing id")
        return issues
