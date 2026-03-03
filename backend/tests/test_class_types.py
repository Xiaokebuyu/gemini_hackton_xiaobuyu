"""Tests for class_types dataclasses and ClassRegistry extensions (Batch 1-2)."""

from app.game_core.content.registries.class_types import (
    Feature,
    ResourceConfig,
    SpellcastingConfig,
)


# ---------------------------------------------------------------------------
# ResourceConfig
# ---------------------------------------------------------------------------


def test_resource_config_defaults() -> None:
    rc = ResourceConfig()
    assert rc.max_at_level == {}
    assert rc.recovery == "long_rest"


def test_resource_config_full() -> None:
    rc = ResourceConfig(max_at_level={"1": 1, "5": 2, "11": 3}, recovery="short_rest")
    assert rc.max_at_level["5"] == 2
    assert rc.recovery == "short_rest"


# ---------------------------------------------------------------------------
# Feature
# ---------------------------------------------------------------------------


def test_feature_defaults() -> None:
    f = Feature()
    assert f.id == ""
    assert f.name == ""
    assert f.type == "passive"
    assert f.skill_id is None
    assert f.resource_config is None


def test_feature_with_resource_config() -> None:
    rc = ResourceConfig(max_at_level={"2": 1, "17": 2}, recovery="short_rest")
    f = Feature(
        id="action_surge",
        name="魔力涌现",
        type="resource",
        resource_config=rc,
    )
    assert f.id == "action_surge"
    assert f.type == "resource"
    assert f.resource_config is not None
    assert f.resource_config.max_at_level["2"] == 1


def test_feature_active_with_skill_id() -> None:
    f = Feature(id="spell_attack", type="active", skill_id="arcana")
    assert f.skill_id == "arcana"
    assert f.resource_config is None


# ---------------------------------------------------------------------------
# SpellcastingConfig
# ---------------------------------------------------------------------------


def test_spellcasting_config_defaults() -> None:
    sc = SpellcastingConfig()
    assert sc.stat == ""
    assert sc.cantrips_known == {}
    assert sc.spell_slots == {}
    assert sc.spells_known is None
    assert sc.prepared_formula is None


def test_spellcasting_config_full() -> None:
    sc = SpellcastingConfig(
        stat="int",
        cantrips_known={"1": 3, "4": 4},
        spell_slots={"1": {"1": 2}, "3": {"1": 4, "2": 2}},
        spells_known={"1": 3, "5": 6},
        prepared_formula=None,
    )
    assert sc.stat == "int"
    assert sc.cantrips_known["4"] == 4
    assert sc.spell_slots["3"]["2"] == 2
    assert sc.spells_known is not None
    assert sc.spells_known["5"] == 6


def test_spellcasting_config_prepared_caster() -> None:
    sc = SpellcastingConfig(stat="wis", prepared_formula="WIS_mod + level")
    assert sc.spells_known is None
    assert sc.prepared_formula == "WIS_mod + level"


# ---------------------------------------------------------------------------
# __init__ re-export
# ---------------------------------------------------------------------------


def test_init_exports() -> None:
    """3 个新类型可从 registries 直接 import。"""
    from app.game_core.content.registries import (  # noqa: F401
        Feature,
        ResourceConfig,
        SpellcastingConfig,
    )


# ---------------------------------------------------------------------------
# ClassRegistry integration tests
# ---------------------------------------------------------------------------


def test_class_registry_spellcasting_subobject() -> None:
    """spellcasting 子对象 → SpellcastingConfig 构建 + 回填 spellcasting_ability。"""
    from app.game_core.content.registries.classes import ClassRegistry

    reg = ClassRegistry()
    reg.load({
        "wizard": {
            "id": "wizard",
            "name": "法师",
            "spellcasting": {
                "stat": "int",
                "cantrips_known": {"1": 3, "4": 4},
                "spell_slots": {"1": {"1": 2}, "3": {"1": 4, "2": 2}},
                "prepared_formula": "INT_mod + level",
            },
        },
    })
    cls = reg.get("wizard")
    assert cls is not None
    assert cls.spellcasting is not None
    assert cls.spellcasting.stat == "int"
    assert cls.spellcasting.spell_slots["3"]["2"] == 2
    assert cls.spellcasting.prepared_formula == "INT_mod + level"
    # backward-compat: flat fields filled from SpellcastingConfig
    assert cls.spellcasting_ability == "int"
    assert cls.prepared_formula == "INT_mod + level"


def test_class_registry_flat_fields_only() -> None:
    """只有拍平字段 → spellcasting=None，拍平字段正常。"""
    from app.game_core.content.registries.classes import ClassRegistry

    reg = ClassRegistry()
    reg.load({
        "fighter": {
            "id": "fighter",
            "name": "战士",
            "spellcasting_ability": "str",
            "prepared_formula": "some_formula",
        },
    })
    cls = reg.get("fighter")
    assert cls is not None
    assert cls.spellcasting is None
    assert cls.spellcasting_ability == "str"
    assert cls.prepared_formula == "some_formula"


def test_class_registry_spellcasting_missing_stat() -> None:
    """stat 缺失 → issue 收集，spellcasting=None。"""
    from app.game_core.content.registries.classes import ClassRegistry

    reg = ClassRegistry()
    reg.load({
        "bad_caster": {
            "id": "bad_caster",
            "spellcasting": {"cantrips_known": {"1": 2}},  # stat absent
        },
    })
    cls = reg.get("bad_caster")
    assert cls is not None
    assert cls.spellcasting is None
    issues = reg.validate()
    assert any("spellcasting missing stat" in i for i in issues)


def test_class_registry_proficiency_fields() -> None:
    """armor/weapon/save proficiency + subclass_options + skill_choices 加载验证。"""
    from app.game_core.content.registries.classes import ClassRegistry

    reg = ClassRegistry()
    reg.load({
        "paladin": {
            "id": "paladin",
            "name": "圣武士",
            "armor_proficiency": ["light", "medium", "heavy", "shield"],
            "weapon_proficiency": ["simple", "martial"],
            "save_proficiency": ["wis", "cha"],
            "subclass_options": ["oath_of_devotion", "oath_of_ancients"],
            "skill_choices": {"choose": 2, "from": ["athletics", "persuasion"]},
            "tags": ["martial", "divine"],
        },
    })
    cls = reg.get("paladin")
    assert cls is not None
    assert "heavy" in cls.armor_proficiency
    assert "martial" in cls.weapon_proficiency
    assert "cha" in cls.save_proficiency
    assert len(cls.subclass_options) == 2
    assert cls.skill_choices["choose"] == 2
    assert "divine" in cls.tags


def test_subclass_template_new_fields() -> None:
    """SubclassTemplate name/description/tags/requirements/additional_proficiency。"""
    from app.game_core.content.registries.classes import ClassRegistry

    reg = ClassRegistry()
    reg.load({
        "classes": {},
        "subclasses": {
            "devotion": {
                "id": "devotion",
                "class_id": "paladin",
                "name": "虔诚誓约",
                "description": "古老誓约的守护者",
                "tags": ["divine", "smite"],
                "requirements": {"level": 3, "class": "paladin"},
                "additional_proficiency": ["celestial"],
            },
        },
    })
    sub = reg.get_subclass("devotion")
    assert sub is not None
    assert sub.name == "虔诚誓约"
    assert sub.description == "古老誓约的守护者"
    assert "divine" in sub.tags
    assert sub.requirements["level"] == 3
    assert "celestial" in sub.additional_proficiency
    assert sub.additional_spellcasting is None


def test_subclass_template_additional_spellcasting() -> None:
    """SubclassTemplate additional_spellcasting 子对象正确构建。"""
    from app.game_core.content.registries.classes import ClassRegistry

    reg = ClassRegistry()
    reg.load({
        "classes": {},
        "subclasses": {
            "land_druid": {
                "id": "land_druid",
                "class_id": "druid",
                "name": "大地德鲁伊",
                "spellcasting": {
                    "stat": "wis",
                    "spell_slots": {"3": {"3": 2}},
                },
            },
        },
    })
    sub = reg.get_subclass("land_druid")
    assert sub is not None
    assert sub.additional_spellcasting is not None
    assert sub.additional_spellcasting.stat == "wis"
    assert sub.additional_spellcasting.spell_slots["3"]["3"] == 2


def test_race_template_new_fields() -> None:
    """RaceTemplate tags/speed/languages/size 加载验证。"""
    from app.game_core.content.registries.classes import ClassRegistry

    reg = ClassRegistry()
    reg.load({
        "classes": {},
        "races": {
            "elf": {
                "id": "elf",
                "name": "精灵",
                "tags": ["fey", "agile"],
                "speed": 35,
                "languages": ["通用语", "精灵语"],
                "size": "medium",
            },
        },
    })
    race = reg.get_race("elf")
    assert race is not None
    assert "fey" in race.tags
    assert race.speed == 35
    assert "精灵语" in race.languages
    assert race.size == "medium"


def test_race_template_default_speed() -> None:
    """speed 缺省时默认为 30。"""
    from app.game_core.content.registries.classes import ClassRegistry

    reg = ClassRegistry()
    reg.load({
        "classes": {},
        "races": {
            "human": {"id": "human", "name": "人类"},
        },
    })
    race = reg.get_race("human")
    assert race is not None
    assert race.speed == 30
    assert race.size == "medium"


def test_background_template_new_fields() -> None:
    """BackgroundTemplate tool_proficiency/equipment 加载验证。"""
    from app.game_core.content.registries.classes import ClassRegistry

    reg = ClassRegistry()
    reg.load({
        "classes": {},
        "backgrounds": {
            "criminal": {
                "id": "criminal",
                "name": "罪犯",
                "tool_proficiency": ["thieves_tools", "gaming_set"],
                "equipment": ["crowbar", "dark_clothes", "gold_15"],
            },
        },
    })
    bg = reg.get_background("criminal")
    assert bg is not None
    assert "thieves_tools" in bg.tool_proficiency
    assert len(bg.tool_proficiency) == 2
    assert "crowbar" in bg.equipment
    assert len(bg.equipment) == 3
