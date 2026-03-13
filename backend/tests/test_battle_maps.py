"""Tests for BattleMapRegistry — synchronous (no asyncio needed)."""

from __future__ import annotations

import json
import random
from pathlib import Path

from app.game_core.content.registries.battle_maps import (
    BattleMapRegistry,
    BattleMapTemplate,
    BattleMapVariant,
)

_BATTLE_MAPS_JSON = (
    Path(__file__).resolve().parent.parent
    / "data" / "goblin_slayer" / "v2" / "battle_maps.json"
)

_VALID_TERRAIN_CHARS = frozenset("GFHSWRBMD")


def _load_registry() -> BattleMapRegistry:
    data = json.loads(_BATTLE_MAPS_JSON.read_text(encoding="utf-8"))
    reg = BattleMapRegistry()
    reg.load(data)
    return reg


# ---------------------------------------------------------------------------
# test_load_battle_maps_registry
# ---------------------------------------------------------------------------

class TestBattleMapRegistryLoad:
    def test_load_battle_maps_registry_category_count(self) -> None:
        reg = _load_registry()
        assert len(reg.list_all()) == 10

    def test_load_battle_maps_registry_known_categories(self) -> None:
        reg = _load_registry()
        for cat in (
            "cave", "ruins", "plains", "woodland", "hills",
            "swamp", "town_street", "temple", "bridge", "camp",
        ):
            tmpl = reg.get(cat)
            assert tmpl is not None, f"category '{cat}' missing"
            assert isinstance(tmpl, BattleMapTemplate)
            assert tmpl.category == cat

    def test_load_battle_maps_registry_no_load_issues(self) -> None:
        reg = _load_registry()
        issues = reg.validate()
        assert issues == [], f"unexpected load issues: {issues}"

    def test_load_each_category_has_two_variants(self) -> None:
        reg = _load_registry()
        for tmpl in reg.list_all():
            assert len(tmpl.variants) == 2, (
                f"category '{tmpl.category}' expected 2 variants, got {len(tmpl.variants)}"
            )

    def test_variants_are_battle_map_variant_instances(self) -> None:
        reg = _load_registry()
        for tmpl in reg.list_all():
            for var in tmpl.variants:
                assert isinstance(var, BattleMapVariant)


# ---------------------------------------------------------------------------
# test_select_variant_returns_valid
# ---------------------------------------------------------------------------

class TestSelectVariant:
    def test_select_variant_returns_valid(self) -> None:
        reg = _load_registry()
        rng = random.Random(42)
        for cat in ("cave", "ruins", "plains", "woodland", "hills"):
            variant = reg.select_variant(cat, rng=rng)
            assert variant is not None
            assert isinstance(variant, BattleMapVariant)
            assert variant.name

    def test_select_variant_unknown_category_returns_none(self) -> None:
        reg = _load_registry()
        result = reg.select_variant("unknown_category_xyz")
        assert result is None

    def test_select_variant_is_deterministic_with_fixed_seed(self) -> None:
        reg = _load_registry()
        v1 = reg.select_variant("cave", rng=random.Random(7))
        v2 = reg.select_variant("cave", rng=random.Random(7))
        assert v1 is not None and v2 is not None
        assert v1.name == v2.name


# ---------------------------------------------------------------------------
# test_select_by_tags_matches
# ---------------------------------------------------------------------------

class TestSelectByTags:
    def test_select_by_tags_matches_cave_indoor(self) -> None:
        reg = _load_registry()
        rng = random.Random(0)
        variant = reg.select_by_tags(["indoor"], rng=rng)
        assert variant is not None
        assert "indoor" in variant.tags

    def test_select_by_tags_matches_outdoor(self) -> None:
        reg = _load_registry()
        rng = random.Random(1)
        variant = reg.select_by_tags(["outdoor"], rng=rng)
        assert variant is not None
        assert "outdoor" in variant.tags

    def test_select_by_tags_unknown_tag_returns_none(self) -> None:
        reg = _load_registry()
        result = reg.select_by_tags(["nonexistent_tag_xyz"])
        assert result is None

    def test_select_by_tags_empty_tags_returns_none(self) -> None:
        reg = _load_registry()
        result = reg.select_by_tags([])
        assert result is None

    def test_select_by_tags_any_match(self) -> None:
        # "cave" tag only on cave category; "hills" only on hills category
        # Requesting both → both categories can match, result is one of them
        reg = _load_registry()
        rng = random.Random(99)
        variant = reg.select_by_tags(["cave", "hills"], rng=rng)
        assert variant is not None
        assert "cave" in variant.tags or "hills" in variant.tags


# ---------------------------------------------------------------------------
# test_variant_spawn_no_overlap
# ---------------------------------------------------------------------------

class TestVariantSpawnNoOverlap:
    def test_variant_spawn_no_overlap(self) -> None:
        reg = _load_registry()
        for tmpl in reg.list_all():
            for var in tmpl.variants:
                ps = set(var.player_spawn)
                es = set(var.enemy_spawn)
                overlap = ps & es
                assert not overlap, (
                    f"category '{tmpl.category}' variant '{var.name}' "
                    f"has spawn overlap: {overlap}"
                )

    def test_variant_spawns_have_three_points_each(self) -> None:
        reg = _load_registry()
        for tmpl in reg.list_all():
            for var in tmpl.variants:
                assert len(var.player_spawn) == 3, (
                    f"category '{tmpl.category}' variant '{var.name}' "
                    f"player_spawn expected 3, got {len(var.player_spawn)}"
                )
                assert len(var.enemy_spawn) == 3, (
                    f"category '{tmpl.category}' variant '{var.name}' "
                    f"enemy_spawn expected 3, got {len(var.enemy_spawn)}"
                )


# ---------------------------------------------------------------------------
# test_variant_terrain_dimensions
# ---------------------------------------------------------------------------

class TestVariantTerrainDimensions:
    def test_variant_terrain_row_count_equals_height(self) -> None:
        reg = _load_registry()
        for tmpl in reg.list_all():
            for var in tmpl.variants:
                assert len(var.terrain) == var.height, (
                    f"category '{tmpl.category}' variant '{var.name}': "
                    f"terrain rows={len(var.terrain)}, height={var.height}"
                )

    def test_variant_terrain_row_length_equals_width(self) -> None:
        reg = _load_registry()
        for tmpl in reg.list_all():
            for var in tmpl.variants:
                for ri, row in enumerate(var.terrain):
                    assert len(row) == var.width, (
                        f"category '{tmpl.category}' variant '{var.name}' "
                        f"row[{ri}]: len={len(row)}, width={var.width}"
                    )

    def test_variant_terrain_chars_valid(self) -> None:
        reg = _load_registry()
        for tmpl in reg.list_all():
            for var in tmpl.variants:
                for ri, row in enumerate(var.terrain):
                    invalid = set(row) - _VALID_TERRAIN_CHARS
                    assert not invalid, (
                        f"category '{tmpl.category}' variant '{var.name}' "
                        f"row[{ri}] invalid chars: {invalid}"
                    )

    def test_all_maps_are_8x6_standard(self) -> None:
        reg = _load_registry()
        for tmpl in reg.list_all():
            for var in tmpl.variants:
                assert var.width == 8, (
                    f"category '{tmpl.category}' variant '{var.name}' width={var.width}, expected 8"
                )
                assert var.height == 6, (
                    f"category '{tmpl.category}' variant '{var.name}' height={var.height}, expected 6"
                )

    def test_spawn_points_within_bounds(self) -> None:
        reg = _load_registry()
        for tmpl in reg.list_all():
            for var in tmpl.variants:
                for sp_field in (var.player_spawn, var.enemy_spawn):
                    for col, row in sp_field:
                        assert 0 <= col < var.width, (
                            f"category '{tmpl.category}' variant '{var.name}' "
                            f"col={col} out of bounds (width={var.width})"
                        )
                        assert 0 <= row < var.height, (
                            f"category '{tmpl.category}' variant '{var.name}' "
                            f"row={row} out of bounds (height={var.height})"
                        )


# ---------------------------------------------------------------------------
# Load validation / edge cases
# ---------------------------------------------------------------------------

class TestBattleMapRegistryValidation:
    def test_load_empty_data(self) -> None:
        reg = BattleMapRegistry()
        reg.load({})
        assert reg.list_all() == []
        assert reg.validate() == []

    def test_load_invalid_terrain_wrong_row_count(self) -> None:
        reg = BattleMapRegistry()
        reg.load({
            "test": {
                "variants": [{
                    "name": "bad_map",
                    "size": [4, 3],
                    "terrain": ["GGGG", "GGGG"],   # only 2 rows, expected 3
                    "player_spawn": [[0, 0]],
                    "enemy_spawn": [[3, 0]],
                    "tags": [],
                }]
            }
        })
        issues = reg.validate()
        assert any("row count" in issue for issue in issues), issues

    def test_load_invalid_terrain_wrong_row_length(self) -> None:
        reg = BattleMapRegistry()
        reg.load({
            "test": {
                "variants": [{
                    "name": "bad_row",
                    "size": [4, 2],
                    "terrain": ["GGG", "GGGG"],    # row[0] has 3 chars, expected 4
                    "player_spawn": [[0, 0]],
                    "enemy_spawn": [[3, 0]],
                    "tags": [],
                }]
            }
        })
        issues = reg.validate()
        assert any("length" in issue for issue in issues), issues

    def test_load_spawn_out_of_bounds(self) -> None:
        reg = BattleMapRegistry()
        reg.load({
            "test": {
                "variants": [{
                    "name": "oob_spawn",
                    "size": [4, 3],
                    "terrain": ["GGGG", "GGGG", "GGGG"],
                    "player_spawn": [[0, 0]],
                    "enemy_spawn": [[4, 0]],   # col=4 is OOB for width=4
                    "tags": [],
                }]
            }
        })
        issues = reg.validate()
        assert any("out of bounds" in issue for issue in issues), issues

    def test_registry_name(self) -> None:
        reg = BattleMapRegistry()
        assert reg.name == "battle_maps"

    def test_shallow_water_char_accepted(self) -> None:
        """Terrain char 'D' (shallow_water) must be accepted by the registry."""
        reg = BattleMapRegistry()
        reg.load({
            "wetlands": {
                "variants": [{
                    "name": "浅水区",
                    "size": [4, 2],
                    "terrain": ["GDDG", "GDDG"],
                    "player_spawn": [[0, 0], [0, 1], [1, 0]],
                    "enemy_spawn": [[3, 0], [3, 1], [2, 0]],
                    "tags": ["wetlands", "outdoor"],
                }]
            }
        })
        issues = reg.validate()
        assert issues == [], f"shallow_water 'D' should be valid: {issues}"


# ---------------------------------------------------------------------------
# New categories: F-1 five new battle map categories
# ---------------------------------------------------------------------------

class TestNewMapCategories:
    """Verify the 5 new map categories (F-1) are correctly loaded."""

    def test_new_categories_all_loaded(self) -> None:
        reg = _load_registry()
        for cat in ("swamp", "town_street", "temple", "bridge", "camp"):
            tmpl = reg.get(cat)
            assert tmpl is not None, f"new category '{cat}' missing"
            assert len(tmpl.variants) == 2, f"'{cat}' should have 2 variants"

    def test_new_categories_no_load_issues(self) -> None:
        reg = _load_registry()
        assert reg.validate() == []

    def test_select_by_tags_swamp(self) -> None:
        reg = _load_registry()
        variant = reg.select_by_tags(["swamp"], rng=random.Random(0))
        assert variant is not None
        assert "swamp" in variant.tags

    def test_select_by_tags_temple_indoor(self) -> None:
        reg = _load_registry()
        # temple variants are tagged "indoor"
        variant = reg.select_by_tags(["temple"], rng=random.Random(0))
        assert variant is not None
        assert "temple" in variant.tags
        assert "indoor" in variant.tags

    def test_select_by_tags_bridge(self) -> None:
        reg = _load_registry()
        variant = reg.select_by_tags(["bridge"], rng=random.Random(0))
        assert variant is not None
        assert "bridge" in variant.tags

    def test_select_by_tags_camp(self) -> None:
        reg = _load_registry()
        variant = reg.select_by_tags(["camp"], rng=random.Random(0))
        assert variant is not None
        assert "camp" in variant.tags

    def test_select_by_tags_town_street(self) -> None:
        reg = _load_registry()
        variant = reg.select_by_tags(["town_street"], rng=random.Random(0))
        assert variant is not None
        assert "town_street" in variant.tags

    def test_new_categories_spawn_no_overlap(self) -> None:
        reg = _load_registry()
        for cat in ("swamp", "town_street", "temple", "bridge", "camp"):
            tmpl = reg.get(cat)
            assert tmpl is not None
            for var in tmpl.variants:
                ps = set(var.player_spawn)
                es = set(var.enemy_spawn)
                overlap = ps & es
                assert not overlap, (
                    f"category '{cat}' variant '{var.name}' "
                    f"has spawn overlap: {overlap}"
                )

    def test_new_categories_three_spawns_each(self) -> None:
        reg = _load_registry()
        for cat in ("swamp", "town_street", "temple", "bridge", "camp"):
            tmpl = reg.get(cat)
            assert tmpl is not None
            for var in tmpl.variants:
                assert len(var.player_spawn) == 3, (
                    f"'{cat}/{var.name}' player_spawn expected 3"
                )
                assert len(var.enemy_spawn) == 3, (
                    f"'{cat}/{var.name}' enemy_spawn expected 3"
                )
