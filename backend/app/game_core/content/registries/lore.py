"""LoreRegistry implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry


@dataclass(slots=True)
class LoreEntry:
    """Typed lore / world-knowledge text block."""

    id: str
    title: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    scope: str = "global"        # global / chapter / area
    scope_id: str | None = None  # chapter_id or area_id when scope != global


@dataclass(slots=True)
class WorldRule:
    """A world rule for AI Osiris / rules filtering by scope."""

    id: str
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    scope: str = "global"        # global / area / faction
    scope_id: str | None = None
    priority: int = 0            # higher = more important


class LoreRegistry(ContentRegistry):
    """Registry for lore and world-rule text blocks."""

    def __init__(self) -> None:
        super().__init__("lore")
        self._items: dict[str, LoreEntry] = {}
        self._rules: dict[str, WorldRule] = {}
        self._load_issues: list[str] = []

    def load(self, data: dict[str, Any]) -> None:
        self._items = {}
        self._rules = {}
        self._load_issues = []

        if isinstance(data, Mapping) and ("entries" in data or "rules" in data):
            raw_entries = self._coerce_dict_mapping(data.get("entries", {}))
            raw_rules = self._coerce_dict_mapping(data.get("rules", {}))
        else:
            raw_entries = self._coerce_dict_mapping(data)
            raw_rules = {}

        for lid, raw in raw_entries.items():
            self._items[lid] = self._build_lore_entry(lid, raw)

        for rid, raw in raw_rules.items():
            rule = self._build_world_rule(rid, raw)
            if rule is not None:
                self._rules[rid] = rule

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

    def get_rule(self, rule_id: str) -> WorldRule | None:
        return self._rules.get(rule_id)

    def list_rules(self) -> list[WorldRule]:
        return list(self._rules.values())

    def list_rules_by_scope(
        self, scope: str, scope_id: str | None = None,
    ) -> list[WorldRule]:
        """Return rules matching scope (and optionally scope_id), sorted by priority desc."""
        results = [r for r in self._rules.values() if r.scope == scope]
        if scope_id is not None:
            results = [r for r in results if r.scope_id == scope_id]
        return sorted(results, key=lambda r: r.priority, reverse=True)

    def get_rules_for_context(
        self,
        *,
        chapter_id: str = "",
        area_id: str = "",
        faction_ids: list[str] | None = None,
    ) -> list[WorldRule]:
        """Get world rules relevant to current context, sorted by priority desc.

        Merges: global rules + chapter rules + area rules + faction rules.
        """
        result: list[WorldRule] = []
        for rule in self._rules.values():
            if rule.scope == "global":
                result.append(rule)
            elif rule.scope == "chapter" and chapter_id and rule.scope_id == chapter_id:
                result.append(rule)
            elif rule.scope == "area" and area_id and rule.scope_id == area_id:
                result.append(rule)
            elif rule.scope == "faction" and faction_ids and rule.scope_id in faction_ids:
                result.append(rule)
        return sorted(result, key=lambda r: r.priority, reverse=True)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        issues = list(self._load_issues)
        for lid, entry in self._items.items():
            if not entry.id:
                issues.append(f"lore entry '{lid}' missing id")
        for rid, rule in self._rules.items():
            if not rule.id:
                issues.append(f"world rule '{rid}' missing id")
        return issues

    # ------------------------------------------------------------------
    # Load helpers
    # ------------------------------------------------------------------

    def _build_lore_entry(self, lid: str, raw: dict[str, Any]) -> LoreEntry:
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

        scope = str(raw.get("scope", "global")).strip() or "global"
        scope_id_raw = raw.get("scope_id")
        scope_id = (
            str(scope_id_raw).strip()
            if scope_id_raw and str(scope_id_raw).strip()
            else None
        )

        return LoreEntry(
            id=str(raw.get("id", lid)),
            title=str(raw.get("name") or raw.get("title") or ""),
            content=str(raw.get("content") or raw.get("text") or ""),
            tags=(
                [str(t) for t in raw_tags if isinstance(t, str) and str(t).strip()]
                if isinstance(raw_tags, list) else []
            ),
            scope=scope,
            scope_id=scope_id,
        )

    def _build_world_rule(self, rule_id: str, raw: dict[str, Any]) -> WorldRule | None:
        rid = self._coerce_non_empty_string(raw.get("id")) or rule_id
        title = str(raw.get("title", "")).strip()
        if not title:
            self._load_issues.append(f"rule '{rule_id}' missing title")
        description = str(raw.get("description", "")).strip()
        raw_tags = raw.get("tags")
        tags = (
            [str(t).strip() for t in raw_tags if str(t).strip()]
            if isinstance(raw_tags, list) else []
        )
        scope = str(raw.get("scope", "global")).strip() or "global"
        scope_id_raw = raw.get("scope_id")
        scope_id = (
            str(scope_id_raw).strip()
            if scope_id_raw and str(scope_id_raw).strip()
            else None
        )
        priority_raw = self._coerce_non_negative_int(raw.get("priority"))
        priority = priority_raw if priority_raw is not None else 0
        return WorldRule(
            id=rid,
            title=title,
            description=description,
            tags=tags,
            scope=scope,
            scope_id=scope_id,
            priority=priority,
        )
