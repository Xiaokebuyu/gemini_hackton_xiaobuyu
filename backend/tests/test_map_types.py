"""Tests for shared_types and map_types dataclasses (Batch 1-1a)."""

from app.game_core.content.registries.map_types import (
    CheckPath,
    ContainerData,
    Discovery,
    HostileConfig,
    HostileGroup,
    HostileTemplate,
    InteractableTemplate,
    SubAreaClusterConfig,
    SubLocationTemplate,
    TrapData,
)
from app.game_core.content.registries.shared_types import Effect, LootTableDef


# ---------------------------------------------------------------------------
# shared_types
# ---------------------------------------------------------------------------


def test_effect_defaults() -> None:
    e = Effect()
    assert e.type == ""
    assert e.params == {}
    assert e.target == ""
    assert e.tags == []


def test_effect_full() -> None:
    e = Effect(type="heal", params={"dice": "2d6", "bonus": 3}, target="self", tags=["restore"])
    assert e.type == "heal"
    assert e.params["dice"] == "2d6"
    assert e.target == "self"
    assert "restore" in e.tags


def test_loot_table_def_defaults() -> None:
    ltd = LootTableDef()
    assert ltd.gold == "0"
    assert ltd.items == []


def test_loot_table_def_full() -> None:
    ltd = LootTableDef(gold="2d6+5", items=["item_a", "item_b"])
    assert ltd.gold == "2d6+5"
    assert len(ltd.items) == 2


# ---------------------------------------------------------------------------
# CheckPath
# ---------------------------------------------------------------------------


def test_check_path_defaults() -> None:
    cp = CheckPath()
    assert cp.skill == ""
    assert cp.dc == 10
    assert cp.label == ""
    assert cp.fail_consequence is None


def test_check_path_full() -> None:
    cp = CheckPath(skill="thieves_tools", dc=15, label="撬锁", fail_consequence="触发陷阱")
    assert cp.skill == "thieves_tools"
    assert cp.dc == 15
    assert cp.fail_consequence == "触发陷阱"


# ---------------------------------------------------------------------------
# TrapData
# ---------------------------------------------------------------------------


def test_trap_data_defaults() -> None:
    t = TrapData()
    assert t.detect_dc == 15
    assert t.disarm_dc == 15
    assert t.damage == "1d6"
    assert t.damage_type == "piercing"
    assert t.effect is None


def test_trap_data_with_effect() -> None:
    t = TrapData(detect_dc=12, disarm_dc=18, damage="2d8", damage_type="poison", effect="中毒 3 回合")
    assert t.damage == "2d8"
    assert t.effect == "中毒 3 回合"


# ---------------------------------------------------------------------------
# ContainerData
# ---------------------------------------------------------------------------


def test_container_data_defaults() -> None:
    c = ContainerData()
    assert c.container_type == "chest"
    assert c.locked is False
    assert c.breakable is False
    assert c.trap is None
    assert isinstance(c.loot, LootTableDef)
    assert c.loot.gold == "0"


def test_container_data_nested() -> None:
    trap = TrapData(damage="2d6", damage_type="fire")
    loot = LootTableDef(gold="1d4")
    c = ContainerData(container_type="barrel", locked=True, breakable=True, trap=trap, loot=loot)
    assert c.container_type == "barrel"
    assert c.locked is True
    assert c.trap is not None
    assert c.trap.damage == "2d6"
    assert c.loot.gold == "1d4"


# ---------------------------------------------------------------------------
# InteractableTemplate
# ---------------------------------------------------------------------------


def test_interactable_template_defaults() -> None:
    ia = InteractableTemplate()
    assert ia.id == ""
    assert ia.type == "inspect"
    assert ia.visibility_dc is None
    assert ia.checks == []
    assert ia.reward is None
    assert ia.one_time is False
    assert ia.container_data is None


def test_interactable_template_with_checks() -> None:
    checks = [
        CheckPath(skill="thieves_tools", dc=15, label="撬锁"),
        CheckPath(skill="athletics", dc=20, label="强行破开"),
    ]
    ia = InteractableTemplate(
        id="chest_01",
        name="古老木箱",
        type="container",
        visibility_dc=12,
        checks=checks,
        one_time=True,
        container_data=ContainerData(locked=True),
    )
    assert ia.id == "chest_01"
    assert ia.visibility_dc == 12
    assert len(ia.checks) == 2
    assert ia.one_time is True
    assert ia.container_data is not None
    assert ia.container_data.locked is True


def test_interactable_template_with_reward() -> None:
    ia = InteractableTemplate(
        id="altar_01",
        reward={"type": "quest_hook", "id": "quest_find_artifact"},
    )
    assert ia.reward is not None
    assert ia.reward["type"] == "quest_hook"


# ---------------------------------------------------------------------------
# HostileGroup / HostileConfig
# ---------------------------------------------------------------------------


def test_hostile_group_defaults() -> None:
    hg = HostileGroup()
    assert hg.monster_ids == []
    assert hg.count == "1"
    assert hg.role == "guard"


def test_hostile_config_defaults() -> None:
    hc = HostileConfig()
    assert hc.hostile_groups == []
    assert hc.stealth_dc == 12
    assert hc.alert_state == "unaware"
    assert hc.blocking is True
    assert hc.ambient_description == ""


def test_hostile_config_full() -> None:
    group = HostileGroup(monster_ids=["goblin", "orc"], count="1d4+1", role="patrol")
    hc = HostileConfig(
        hostile_groups=[group],
        stealth_dc=15,
        alert_state="patrolling",
        blocking=False,
        ambient_description="远处传来脚步声",
    )
    assert len(hc.hostile_groups) == 1
    assert hc.hostile_groups[0].count == "1d4+1"
    assert hc.stealth_dc == 15
    assert hc.blocking is False


# ---------------------------------------------------------------------------
# HostileTemplate
# ---------------------------------------------------------------------------


def test_hostile_template_defaults() -> None:
    ht = HostileTemplate()
    assert ht.id == ""
    assert isinstance(ht.hostile_config, HostileConfig)
    assert ht.interactables == []
    assert ht.refresh_delay_ticks == 12
    assert ht.min_danger == 0.0


def test_hostile_template_full() -> None:
    group = HostileGroup(monster_ids=["troll"], count="1", role="boss")
    hc = HostileConfig(hostile_groups=[group], stealth_dc=18, alert_state="alert")
    ia = InteractableTemplate(id="boss_chest", type="container")
    ht = HostileTemplate(
        id="dungeon_boss_room",
        name="BOSS 房间",
        hostile_config=hc,
        interactables=[ia],
        refresh_delay_ticks=48,
        min_danger=0.8,
    )
    assert ht.id == "dungeon_boss_room"
    assert ht.hostile_config.stealth_dc == 18
    assert len(ht.interactables) == 1
    assert ht.min_danger == 0.8


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discovery_defaults() -> None:
    d = Discovery()
    assert d.id == ""
    assert d.check_type == "perception"
    assert d.dc == 15
    assert d.reward == {}
    assert d.tags == []


def test_discovery_full() -> None:
    d = Discovery(
        id="hidden_passage",
        name="隐秘通道",
        check_type="investigation",
        dc=18,
        reward={"type": "sub_location", "id": "secret_room"},
        tags=["secret", "dungeon"],
    )
    assert d.dc == 18
    assert d.reward["id"] == "secret_room"
    assert "secret" in d.tags


# ---------------------------------------------------------------------------
# SubAreaClusterConfig
# ---------------------------------------------------------------------------


def test_sub_area_cluster_config_defaults() -> None:
    cfg = SubAreaClusterConfig()
    assert cfg.max_dynamic == 5
    assert cfg.max_permanent_dynamic == 2
    assert cfg.fill_tags == []
    assert cfg.fill_density == "normal"


def test_sub_area_cluster_config_full() -> None:
    cfg = SubAreaClusterConfig(
        max_dynamic=8,
        max_permanent_dynamic=3,
        fill_tags=["tavern", "shop"],
        fill_density="dense",
    )
    assert cfg.max_dynamic == 8
    assert cfg.fill_density == "dense"


# ---------------------------------------------------------------------------
# SubLocationTemplate
# ---------------------------------------------------------------------------


def test_sub_location_template_defaults() -> None:
    s = SubLocationTemplate()
    assert s.id == ""
    assert s.type == "visit"
    assert s.available_hours is None
    assert s.resident_npcs == []
    assert s.interactables == []
    assert s.hostile_config is None


def test_sub_location_template_full() -> None:
    ia = InteractableTemplate(id="notice_board", type="inspect")
    hc = HostileConfig(stealth_dc=10, blocking=False)
    s = SubLocationTemplate(
        id="tavern",
        name="铁锅酒馆",
        description="喧闹的村庄酒馆",
        tags=["rest", "social"],
        type="rest",
        available_hours=(8, 23),
        resident_npcs=["npc_barkeep", "npc_bard"],
        interactables=[ia],
        hostile_config=hc,
    )
    assert s.id == "tavern"
    assert s.available_hours == (8, 23)
    assert len(s.resident_npcs) == 2
    assert len(s.interactables) == 1
    assert s.hostile_config is not None
    assert s.hostile_config.stealth_dc == 10


def test_sub_location_template_available_hours_none() -> None:
    """available_hours=None 表示 24h 开放。"""
    s = SubLocationTemplate(id="outdoor_market", available_hours=None)
    assert s.available_hours is None


# ---------------------------------------------------------------------------
# __init__ re-export
# ---------------------------------------------------------------------------


def test_init_exports() -> None:
    """所有新类型可从 registries.__init__ 直接 import。"""
    from app.game_core.content.registries import (  # noqa: F401
        CheckPath,
        ContainerData,
        Discovery,
        Effect,
        HostileConfig,
        HostileGroup,
        HostileTemplate,
        InteractableTemplate,
        LootTableDef,
        SubAreaClusterConfig,
        SubLocationTemplate,
        TrapData,
    )


# ---------------------------------------------------------------------------
# MapRegistry integration tests (Batch 1-1b)
# ---------------------------------------------------------------------------


def test_map_registry_loads_sub_location_as_typed() -> None:
    """MapRegistry.load() 将 sub_locations dict 转换为 SubLocationTemplate。"""
    from app.game_core.content.registries.maps import MapRegistry

    reg = MapRegistry()
    reg.load({
        "town": {
            "id": "town",
            "sub_locations": {
                "inn": {"id": "inn", "name": "铁锅酒馆", "type": "rest"},
            },
        },
    })
    area = reg.get("town")
    assert area is not None
    assert "inn" in area.sub_locations
    sl = area.sub_locations["inn"]
    assert isinstance(sl, SubLocationTemplate)
    assert sl.name == "铁锅酒馆"
    assert sl.type == "rest"


def test_map_registry_loads_sub_locations_list_format() -> None:
    """list 格式的 sub_locations 也正确转换为 SubLocationTemplate。"""
    from app.game_core.content.registries.maps import MapRegistry

    reg = MapRegistry()
    reg.load({
        "forest": {
            "id": "forest",
            "sub_locations": [
                {"id": "camp", "name": "营地"},
                {"id": "shrine", "name": "神祠"},
            ],
        },
    })
    area = reg.get("forest")
    assert area is not None
    assert "camp" in area.sub_locations
    assert area.sub_locations["camp"].name == "营地"
    assert "shrine" in area.sub_locations


def test_map_registry_loads_sub_location_with_interactables_and_hostile() -> None:
    """sub_location 含 interactables + hostile_config 的完整路径。"""
    from app.game_core.content.registries.maps import MapRegistry

    reg = MapRegistry()
    reg.load({
        "dungeon": {
            "id": "dungeon",
            "sub_locations": {
                "boss_room": {
                    "id": "boss_room",
                    "name": "BOSS 房间",
                    "type": "dungeon",
                    "interactables": [
                        {
                            "id": "treasure_chest",
                            "type": "container",
                            "container_data": {
                                "locked": True,
                                "loot": {"gold": "2d6+5"},
                            },
                        }
                    ],
                    "hostile_config": {
                        "stealth_dc": 18,
                        "alert_state": "alert",
                        "hostile_groups": [
                            {"monster_ids": ["dragon"], "count": "1", "role": "boss"}
                        ],
                    },
                }
            },
        },
    })
    area = reg.get("dungeon")
    assert area is not None
    sl = area.sub_locations.get("boss_room")
    assert sl is not None
    assert sl.hostile_config is not None
    assert sl.hostile_config.stealth_dc == 18
    assert len(sl.hostile_config.hostile_groups) == 1
    assert sl.hostile_config.hostile_groups[0].role == "boss"
    assert len(sl.interactables) == 1
    assert sl.interactables[0].type == "container"
    assert sl.interactables[0].container_data is not None
    assert sl.interactables[0].container_data.locked is True
    assert sl.interactables[0].container_data.loot.gold == "2d6+5"


def test_map_registry_loads_area_level_new_fields() -> None:
    """AreaTemplate 新字段 description / danger_level / discoveries / hostile_pool / sub_area_cluster_config。"""
    from app.game_core.content.registries.maps import MapRegistry

    reg = MapRegistry()
    reg.load({
        "wilds": {
            "id": "wilds",
            "description": "荒野之地",
            "danger_level": "high",
            "discoveries": [
                {"id": "hidden_cave", "name": "隐秘山洞", "dc": 18, "reward": {"type": "sub_location", "id": "cave"}}
            ],
            "hostile_pool": [
                {
                    "id": "patrol_group",
                    "name": "巡逻队",
                    "hostile_config": {"stealth_dc": 14},
                    "refresh_delay_ticks": 24,
                    "min_danger": 0.5,
                }
            ],
            "sub_area_cluster_config": {
                "max_dynamic": 8,
                "fill_density": "dense",
            },
        },
    })
    area = reg.get("wilds")
    assert area is not None
    assert area.description == "荒野之地"
    assert area.danger_level == "high"
    assert len(area.discoveries) == 1
    assert area.discoveries[0].id == "hidden_cave"
    assert area.discoveries[0].dc == 18
    assert area.hostile_pool is not None
    assert len(area.hostile_pool) == 1
    assert area.hostile_pool[0].min_danger == 0.5
    assert area.sub_area_cluster_config is not None
    assert area.sub_area_cluster_config.max_dynamic == 8
    assert area.sub_area_cluster_config.fill_density == "dense"


def test_map_registry_sub_location_missing_id_issue() -> None:
    """sub_location 缺 id → 不 crash，收集 issue，该条跳过。"""
    from app.game_core.content.registries.maps import MapRegistry

    reg = MapRegistry()
    reg.load({
        "town": {
            "id": "town",
            "sub_locations": [
                {"name": "无 id 的地点"},     # list 格式，缺 id
                {"id": "inn", "name": "酒馆"},  # 正常
            ],
        },
    })
    area = reg.get("town")
    assert area is not None
    issues = reg.validate()
    assert any("missing id" in i for i in issues)
    assert "inn" in area.sub_locations
    assert len(area.sub_locations) == 1  # 缺 id 那条被跳过


def test_map_registry_sub_location_invalid_available_hours_issue() -> None:
    """available_hours 格式非法 → 收集 issue，字段为 None。"""
    from app.game_core.content.registries.maps import MapRegistry

    reg = MapRegistry()
    reg.load({
        "town": {
            "id": "town",
            "sub_locations": {
                "shop": {"id": "shop", "available_hours": ["not", "a", "number"]},
            },
        },
    })
    area = reg.get("town")
    assert area is not None
    sl = area.sub_locations.get("shop")
    assert sl is not None
    assert sl.available_hours is None
    issues = reg.validate()
    assert any("available_hours" in i for i in issues)


def test_map_registry_available_hours_valid() -> None:
    """available_hours 合法格式 → 正确解析为 tuple。"""
    from app.game_core.content.registries.maps import MapRegistry

    reg = MapRegistry()
    reg.load({
        "town": {
            "id": "town",
            "sub_locations": {
                "tavern": {"id": "tavern", "available_hours": [9, 23]},
            },
        },
    })
    area = reg.get("town")
    assert area is not None
    assert area.sub_locations["tavern"].available_hours == (9, 23)
