"""BattleMapRegistry — static battle map template registry.

Each category holds multiple variants (e.g. cave: tunnel / hall).
select_variant() / select_by_tags() provide random selection for use
at combat-start time.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry

_VALID_TERRAIN_CHARS = frozenset("GFHSWRBMD")


@dataclass(slots=True, frozen=True)
class BattleMapVariant:
    """One concrete map layout within a category."""

    name: str
    width: int
    height: int
    terrain: tuple[str, ...]            # one string per row
    player_spawn: tuple[tuple[int, int], ...]   # (col, row) positions
    enemy_spawn: tuple[tuple[int, int], ...]    # (col, row) positions
    tags: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class BattleMapTemplate:
    """A named category of battle map with multiple variants."""

    category: str
    variants: tuple[BattleMapVariant, ...]


class BattleMapRegistry(ContentRegistry):
    """Registry for battle map templates."""

    def __init__(self) -> None:
        super().__init__("battle_maps")
        self._items: dict[str, BattleMapTemplate] = {}
        self._load_issues: list[str] = []

    # ------------------------------------------------------------------
    # ContentRegistry interface
    # ------------------------------------------------------------------

    def load(self, data: dict[str, Any]) -> None:
        self._items = {}
        self._load_issues = []
        if not isinstance(data, Mapping):
            self._load_issues.append("battle_maps data must be a mapping")
            return

        for category, raw_template in data.items():
            category_str = str(category).strip()
            if not category_str:
                self._load_issues.append("battle_maps has empty category key")
                continue
            if not isinstance(raw_template, Mapping):
                self._load_issues.append(f"category '{category_str}' value must be a mapping")
                continue

            raw_variants = raw_template.get("variants")
            if not isinstance(raw_variants, list):
                self._load_issues.append(f"category '{category_str}' missing variants list")
                continue

            parsed: list[BattleMapVariant] = []
            for idx, raw_var in enumerate(raw_variants):
                variant = self._load_variant(category_str, idx, raw_var)
                if variant is not None:
                    parsed.append(variant)

            if not parsed:
                self._load_issues.append(
                    f"category '{category_str}' has no valid variants after loading"
                )
                continue

            self._items[category_str] = BattleMapTemplate(
                category=category_str,
                variants=tuple(parsed),
            )

    def get(self, content_id: str) -> BattleMapTemplate | None:
        return self._items.get(content_id)

    def list_all(self) -> list[BattleMapTemplate]:
        return list(self._items.values())

    def validate(self) -> list[str]:
        return list(self._load_issues)

    # ------------------------------------------------------------------
    # Domain-specific query helpers
    # ------------------------------------------------------------------

    def select_variant(
        self,
        category: str,
        rng: random.Random | None = None,
    ) -> BattleMapVariant | None:
        """Randomly select one variant from the given category.

        Returns None if the category does not exist.
        """
        template = self._items.get(category)
        if template is None:
            return None
        _rng = rng or random.Random()
        return _rng.choice(template.variants)

    def select_by_tags(
        self,
        tags: list[str],
        rng: random.Random | None = None,
    ) -> BattleMapVariant | None:
        """Select a random variant whose tags intersect with the requested tags.

        Performs an any-match across all categories.  Returns None if no variant
        matches.
        """
        required = {str(t) for t in tags if str(t).strip()}
        if not required:
            return None

        matches: list[BattleMapVariant] = []
        for template in self._items.values():
            for variant in template.variants:
                if required.intersection(set(variant.tags)):
                    matches.append(variant)

        if not matches:
            return None
        _rng = rng or random.Random()
        return _rng.choice(matches)

    # ------------------------------------------------------------------
    # Load helpers
    # ------------------------------------------------------------------

    def _load_variant(
        self,
        category: str,
        idx: int,
        raw: Any,
    ) -> BattleMapVariant | None:
        label = f"category '{category}' variant[{idx}]"
        if not isinstance(raw, Mapping):
            self._load_issues.append(f"{label} must be a mapping")
            return None

        name_raw = self._coerce_non_empty_string(raw.get("name"))
        if name_raw is None:
            self._load_issues.append(f"{label} missing or empty name")
            return None

        # size: [width, height]
        size = raw.get("size")
        if not isinstance(size, (list, tuple)) or len(size) < 2:
            self._load_issues.append(f"{label} '{name_raw}' missing size [w, h]")
            return None
        width_val = self._coerce_positive_int(size[0])
        height_val = self._coerce_positive_int(size[1])
        if width_val is None or height_val is None:
            self._load_issues.append(f"{label} '{name_raw}' size values must be positive ints")
            return None

        # terrain
        raw_terrain = raw.get("terrain")
        if not isinstance(raw_terrain, list):
            self._load_issues.append(f"{label} '{name_raw}' terrain must be a list of strings")
            return None
        if len(raw_terrain) != height_val:
            self._load_issues.append(
                f"{label} '{name_raw}' terrain row count {len(raw_terrain)} != height {height_val}"
            )
            return None
        terrain_rows: list[str] = []
        for row_idx, row in enumerate(raw_terrain):
            row_str = str(row)
            if len(row_str) != width_val:
                self._load_issues.append(
                    f"{label} '{name_raw}' terrain[{row_idx}] length {len(row_str)} != width {width_val}"
                )
                return None
            invalid = set(row_str) - _VALID_TERRAIN_CHARS
            if invalid:
                self._load_issues.append(
                    f"{label} '{name_raw}' terrain[{row_idx}] contains invalid chars {invalid}"
                )
                return None
            terrain_rows.append(row_str)

        # player_spawn / enemy_spawn
        player_spawn = self._load_spawn_points(label, name_raw, raw, "player_spawn", width_val, height_val)
        enemy_spawn = self._load_spawn_points(label, name_raw, raw, "enemy_spawn", width_val, height_val)
        if player_spawn is None or enemy_spawn is None:
            return None

        # tags
        raw_tags = raw.get("tags")
        tags_list: list[str] = []
        if isinstance(raw_tags, list):
            for t in raw_tags:
                s = self._coerce_non_empty_string(t)
                if s:
                    tags_list.append(s)

        return BattleMapVariant(
            name=name_raw,
            width=width_val,
            height=height_val,
            terrain=tuple(terrain_rows),
            player_spawn=tuple(player_spawn),
            enemy_spawn=tuple(enemy_spawn),
            tags=tuple(tags_list),
        )

    def _load_spawn_points(
        self,
        label: str,
        name: str,
        raw: Mapping[str, Any],
        field: str,
        width: int,
        height: int,
    ) -> list[tuple[int, int]] | None:
        raw_spawns = raw.get(field)
        if not isinstance(raw_spawns, list) or not raw_spawns:
            self._load_issues.append(
                f"{label} '{name}' {field} must be a non-empty list of [col, row] pairs"
            )
            return None
        result: list[tuple[int, int]] = []
        for sp_idx, sp in enumerate(raw_spawns):
            if not isinstance(sp, (list, tuple)) or len(sp) < 2:
                self._load_issues.append(
                    f"{label} '{name}' {field}[{sp_idx}] must be [col, row]"
                )
                return None
            col_v = self._coerce_non_negative_int(sp[0])
            row_v = self._coerce_non_negative_int(sp[1])
            if col_v is None or row_v is None:
                self._load_issues.append(
                    f"{label} '{name}' {field}[{sp_idx}] contains non-integer values"
                )
                return None
            if col_v >= width or row_v >= height:
                self._load_issues.append(
                    f"{label} '{name}' {field}[{sp_idx}] ({col_v},{row_v}) is out of bounds "
                    f"for {width}×{height} grid"
                )
                return None
            result.append((col_v, row_v))
        return result
