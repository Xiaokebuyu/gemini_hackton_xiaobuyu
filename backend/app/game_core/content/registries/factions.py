"""FactionRegistry implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry


@dataclass(slots=True)
class FactionTemplate:
    """Typed faction definition."""

    id: str
    name: str = ""
    description: str = ""
    alignment: str = ""
    faction_relations: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    behavioral_rules: str = ""
    initial_standing: int | None = None
    base_standing: int | None = None


class FactionRegistry(ContentRegistry):
    """Registry for faction definitions."""

    def __init__(self) -> None:
        super().__init__("factions")
        self._items: dict[str, FactionTemplate] = {}
        self._load_issues: list[str] = []

    def load(self, data: dict[str, Any]) -> None:
        self._items = {}
        self._load_issues = []
        coerced = self._coerce_dict_mapping(data)
        for fid, raw in coerced.items():
            for field_name in ("name", "description", "alignment"):
                raw_val = raw.get(field_name)
                if raw_val is not None and self._coerce_non_empty_string(raw_val) is None:
                    self._load_issues.append(f"faction '{fid}' has invalid {field_name}")

            # Merge "relations" legacy key → "faction_relations" (design spec canonical name)
            raw_relations = raw.get("faction_relations", raw.get("relations"))
            if raw_relations is not None and not isinstance(raw_relations, Mapping):
                self._load_issues.append(f"faction '{fid}' has invalid faction_relations")

            raw_tags = raw.get("tags")
            if raw_tags is not None:
                if not isinstance(raw_tags, list):
                    self._load_issues.append(f"faction '{fid}' has invalid tags")
                else:
                    for index, tag in enumerate(raw_tags):
                        if self._coerce_non_empty_string(tag) is None:
                            self._load_issues.append(
                                f"faction '{fid}' tags[{index}] must be a non-empty string"
                            )

            self._items[fid] = FactionTemplate(
                id=str(raw.get("id", fid)),
                name=str(raw.get("name") or ""),
                description=str(raw.get("description") or ""),
                alignment=str(raw.get("alignment") or ""),
                faction_relations=(
                    dict(raw_relations) if isinstance(raw_relations, Mapping) else {}
                ),
                tags=(
                    [str(t) for t in raw_tags if isinstance(t, str) and str(t).strip()]
                    if isinstance(raw_tags, list) else []
                ),
                behavioral_rules=str(raw.get("behavioral_rules") or ""),
                initial_standing=self._coerce_non_negative_int(raw.get("initial_standing")),
                base_standing=self._coerce_non_negative_int(raw.get("base_standing")),
            )

    def get(self, content_id: str) -> FactionTemplate | None:
        return self._items.get(content_id)

    def list_all(self) -> list[FactionTemplate]:
        return list(self._items.values())

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_by_tag(self, tag: str) -> list[FactionTemplate]:
        """Return factions that have the given tag in their tags list."""
        return [item for item in self._items.values() if tag in item.tags]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        issues = list(self._load_issues)
        for fid, faction in self._items.items():
            if not faction.id:
                issues.append(f"faction '{fid}' missing id")
        return issues
