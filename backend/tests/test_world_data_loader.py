"""Tests for world_data_loader (T-2): goblin_slayer data pipeline."""

from __future__ import annotations

from app.world_data_loader import load_goblin_slayer_world_data
from app.game_core.bootstrap import build_default_world


_DATA = load_goblin_slayer_world_data()


# ---------------------------------------------------------------------------
# Characters
# ---------------------------------------------------------------------------

def test_characters_have_area_id_not_default_map() -> None:
    """default_map must be renamed to area_id."""
    chars = _DATA["characters"]
    assert len(chars) > 0
    for ch in chars:
        assert "default_map" not in ch
        assert "area_id" in ch


def test_characters_have_location_id_not_default_sub_location() -> None:
    """default_sub_location must be renamed to location_id."""
    chars = _DATA["characters"]
    for ch in chars:
        assert "default_sub_location" not in ch
        assert "location_id" in ch


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def test_items_is_flat_list_of_dicts() -> None:
    """After flattening, items must be a flat list (no nested lists)."""
    items = _DATA["items"]
    assert len(items) > 0
    for item in items:
        assert isinstance(item, dict), f"Expected dict, got {type(item).__name__}: {item!r}"


def test_items_all_have_id() -> None:
    """Every item dict must carry an id field."""
    for item in _DATA["items"]:
        assert "id" in item


# ---------------------------------------------------------------------------
# Maps
# ---------------------------------------------------------------------------

def test_maps_connections_preserve_metadata() -> None:
    """connections must be preserved as dict objects (not stripped to strings).

    世界加载器不再剥离 connection 元数据，MapRegistry 负责解析。
    """
    maps = _DATA["maps"]
    assert len(maps) > 0
    for area in maps:
        for conn in area.get("connections", []):
            assert isinstance(conn, dict), f"Expected dict connection, got {conn!r}"
            assert "target_map_id" in conn, f"Connection missing target_map_id: {conn!r}"


def test_maps_sub_locations_preserved() -> None:
    """sub_locations must still be present (list or dict)."""
    maps = _DATA["maps"]
    for area in maps:
        if "sub_locations" in area:
            assert area["sub_locations"] is not None


# ---------------------------------------------------------------------------
# Quests
# ---------------------------------------------------------------------------

def test_quests_chapters_nonempty_with_required_keys() -> None:
    """chapters list must be non-empty and each item must have id and title."""
    quests = _DATA["quests"]
    chapters = quests.get("chapters", [])
    assert len(chapters) > 0
    for ch in chapters:
        assert "id" in ch
        assert "title" in ch


def test_quests_milestones_have_chapter_id() -> None:
    """Each milestone must reference a chapter via chapter_id."""
    quests = _DATA["quests"]
    milestones = quests.get("milestones", {})
    assert len(milestones) > 0
    for ms in milestones.values():
        assert "chapter_id" in ms
        assert ms["chapter_id"]


# ---------------------------------------------------------------------------
# End-to-end smoke
# ---------------------------------------------------------------------------

def test_world_load_all_does_not_raise() -> None:
    """WorldInstance.load_all() with goblin_slayer data must not raise."""
    world = build_default_world("goblin_slayer", world_data=_DATA)
    assert world is not None
    # Registry sanity checks
    chars = world.get_registry("characters")
    assert chars is not None
    assert len(chars.list_all()) > 0

    maps = world.get_registry("maps")
    assert maps is not None
    assert len(maps.list_all()) > 0


# ---------------------------------------------------------------------------
# Monsters（M1-M5）
# ---------------------------------------------------------------------------

def test_monsters_stats_flattened() -> None:
    """hp/ac 必须从 stats 子对象展平到顶层。"""
    combat = [m for m in _DATA["monsters"] if m.get("hp", 0) > 0]
    assert len(combat) >= 10
    for m in combat:
        assert m["hp"] > 0
        assert m.get("ac", 0) > 0


def test_monsters_abilities_present() -> None:
    """战斗怪物必须有非空的 abilities Mapping。"""
    for m in _DATA["monsters"]:
        if m.get("hp", 0) > 0:
            assert isinstance(m.get("abilities"), dict)
            assert m["abilities"]  # 非空


def test_monsters_creature_type_mapped() -> None:
    """战斗怪物必须有非空 creature_type（从 type 字段别名）。"""
    for m in _DATA["monsters"]:
        if m.get("hp", 0) > 0:
            assert m.get("creature_type")


def test_monsters_attacks_use_damage_dice_key() -> None:
    """attacks 条目必须用 damage_dice，旧 damage 键必须已重命名。"""
    for m in _DATA["monsters"]:
        for atk in m.get("attacks", []):
            assert "damage_dice" in atk, f"attack missing damage_dice: {atk}"
            assert "damage" not in atk


def test_monsters_hp_zero_entries_filtered() -> None:
    """hp=0 的规则描述条目必须被过滤掉。"""
    for m in _DATA["monsters"]:
        assert m.get("hp", 1) > 0, f"hp=0 entry not filtered: {m.get('id')}"


def test_monsters_cr_numeric_or_none() -> None:
    """cr 字段必须是 float/int 或 None（不能是中文字符串）。"""
    for m in _DATA["monsters"]:
        cr = m.get("cr")
        assert cr is None or isinstance(cr, (int, float))


# ---------------------------------------------------------------------------
# Skills（S1）
# ---------------------------------------------------------------------------

def test_skills_includes_nested_entries() -> None:
    """递归展平后技能总数必须 ≥ 50（含嵌套列表中的 108 条角色技能）。"""
    assert len(_DATA["skills"]) >= 50


# ---------------------------------------------------------------------------
# Items（I1）
# ---------------------------------------------------------------------------

def test_items_have_parseable_base_price() -> None:
    """有 price 字符串的物品应能解析到 base_price int（银币）。"""
    priced = [i for i in _DATA["items"] if isinstance(i.get("price"), str) and i["price"].strip()]
    parsed = [i for i in priced if isinstance(i.get("base_price"), int)]
    assert len(parsed) > 0
