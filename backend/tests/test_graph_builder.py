"""GraphBuilder 单元测试 -- Step C3

用 mock WorldInstance + SessionRuntime 验证图构建逻辑。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.models.narrative import (
    Chapter,
    ChapterObjective,
    ChapterTransition,
    Condition,
    ConditionGroup,
    ConditionType,
    StoryEvent,
)
from app.models.party import Party, PartyMember, TeammateRole
from app.runtime.models.area_state import AreaConnection, AreaDefinition, SubLocationDef
from app.runtime.models.world_constants import WorldConstants
from app.world.graph_builder import (
    GraphBuilder,
    _event_to_behaviors,
    _extract_char_field,
    _sanitize_region_id,
)
from app.world.models import (
    EventStatus,
    WorldEdgeType,
    WorldNodeType,
)


# =============================================================================
# Fixtures: mock data
# =============================================================================


def _make_world_constants() -> WorldConstants:
    return WorldConstants(
        world_id="test_world",
        name="Test World",
        description="A test world for unit tests.",
        setting="Fantasy",
        tone="Dark",
    )


def _make_area_registry() -> dict:
    return {
        "town_square": AreaDefinition(
            area_id="town_square",
            name="Town Square",
            description="The central square of the town.",
            danger_level=1,
            area_type="settlement",
            region="Frontier",
            tags=["town", "safe"],
            key_features=["Fountain", "Market"],
            available_actions=["explore", "talk"],
            sub_locations=[
                SubLocationDef(
                    id="tavern",
                    name="The Rusty Goblet",
                    description="A cozy tavern.",
                    interaction_type="social",
                    available_actions=["drink", "talk"],
                    passerby_spawn_rate=0.3,
                ),
                SubLocationDef(
                    id="blacksmith",
                    name="Iron Forge",
                    description="A blacksmith shop.",
                    interaction_type="trade",
                ),
            ],
            connections=[
                AreaConnection(
                    target_area_id="dark_forest",
                    connection_type="road",
                    travel_time="1 hour",
                ),
            ],
        ),
        "dark_forest": AreaDefinition(
            area_id="dark_forest",
            name="Dark Forest",
            description="A foreboding forest.",
            danger_level=3,
            area_type="wilderness",
            region="Frontier",
            tags=["dangerous", "forest"],
            connections=[
                AreaConnection(
                    target_area_id="town_square",
                    connection_type="road",
                    travel_time="1 hour",
                ),
                AreaConnection(
                    target_area_id="goblin_cave",
                    connection_type="hidden_path",
                    travel_time="30 minutes",
                ),
            ],
        ),
        "goblin_cave": AreaDefinition(
            area_id="goblin_cave",
            name="Goblin Cave",
            description="A dark cave.",
            danger_level=4,
            area_type="dungeon",
            region="Underground",
        ),
    }


def _make_chapter_registry() -> dict:
    return {
        "ch_01": {
            "id": "ch_01",
            "name": "Chapter 1: Arrival",
            "description": "The adventurer arrives at town.",
            "mainline_id": "main_01",
            "status": "active",
            "available_maps": ["town_square", "dark_forest"],
            "objectives": [
                {"id": "obj_explore", "description": "Explore the town"},
            ],
            "events": [
                {
                    "id": "evt_meet_npc",
                    "name": "Meet the Blacksmith",
                    "description": "First encounter with the blacksmith.",
                    "trigger_conditions": {
                        "operator": "and",
                        "conditions": [
                            {"type": "location", "params": {"area_id": "town_square"}},
                        ],
                    },
                    "completion_conditions": {
                        "operator": "and",
                        "conditions": [
                            {"type": "npc_interacted", "params": {"npc_id": "npc_blacksmith"}},
                        ],
                    },
                    "on_complete": {
                        "unlock_events": ["evt_forest_quest"],
                        "add_xp": 50,
                    },
                    "is_required": True,
                    "narrative_directive": "Introduce the blacksmith.",
                },
                {
                    "id": "evt_forest_quest",
                    "name": "Forest Exploration",
                    "description": "Explore the dark forest.",
                    "trigger_conditions": {
                        "operator": "and",
                        "conditions": [],
                    },
                    "is_required": False,
                    "narrative_directive": "Guide player into the forest.",
                },
            ],
            "transitions": [
                {
                    "target_chapter_id": "ch_02",
                    "conditions": {
                        "operator": "and",
                        "conditions": [
                            {"type": "event_triggered", "params": {"event_id": "evt_meet_npc"}},
                        ],
                    },
                    "priority": 0,
                    "transition_type": "normal",
                    "narrative_hint": "The adventure deepens.",
                },
            ],
            "pacing": {"min_rounds": 3, "ideal_rounds": 10, "max_rounds": 30},
            "tags": ["intro"],
        },
        "ch_02": {
            "id": "ch_02",
            "name": "Chapter 2: The Cave",
            "description": "Venture into the goblin cave.",
            "mainline_id": "main_01",
            "status": "locked",
            "available_maps": ["goblin_cave"],
            "events": [],
            "transitions": [],
            "tags": ["combat"],
        },
    }


def _make_character_registry() -> dict:
    return {
        "npc_blacksmith": {
            "id": "npc_blacksmith",
            "profile": {
                "name": "Grom",
                "metadata": {
                    "default_map": "town_square",
                    "default_sub_location": "blacksmith",
                    "tier": "secondary",
                },
                "personality": "Gruff but kind.",
                "occupation": "Blacksmith",
                "backstory": "Former adventurer.",
                "relationships": [
                    {
                        "character_id": "npc_innkeeper",
                        "type": "friend",
                        "description": "Old drinking buddy.",
                    },
                ],
            },
        },
        "npc_innkeeper": {
            "id": "npc_innkeeper",
            "profile": {
                "name": "Elena",
                "metadata": {
                    "default_map": "town_square",
                    "default_sub_location": "tavern",
                    "tier": "main",
                },
                "personality": "Warm and chatty.",
                "occupation": "Innkeeper",
            },
        },
        "npc_hermit": {
            "id": "npc_hermit",
            "name": "Old Man",
            "default_map": "dark_forest",
            "tier": "passerby",
            "profile": {
                "personality": "Mysterious.",
            },
        },
    }


def _make_session(with_party: bool = True) -> MagicMock:
    session = MagicMock(spec=["party"])
    if with_party:
        session.party = Party(
            party_id="p_01",
            world_id="test_world",
            session_id="s_01",
            leader_id="player_01",
            members=[
                PartyMember(
                    character_id="npc_innkeeper",
                    name="Elena",
                    role=TeammateRole.HEALER,
                    is_active=True,
                ),
                PartyMember(
                    character_id="npc_blacksmith",
                    name="Grom",
                    role=TeammateRole.WARRIOR,
                    is_active=True,
                ),
                PartyMember(
                    character_id="npc_ghost",
                    name="Ghost",
                    role=TeammateRole.SCOUT,
                    is_active=False,
                ),
            ],
        )
    else:
        session.party = None
    return session


def _make_world(
    area_registry=None,
    chapter_registry=None,
    character_registry=None,
) -> MagicMock:
    world = MagicMock(spec=[
        "world_id",
        "world_constants",
        "area_registry",
        "chapter_registry",
        "character_registry",
    ])
    world.world_id = "test_world"
    world.world_constants = _make_world_constants()
    world.area_registry = area_registry if area_registry is not None else _make_area_registry()
    world.chapter_registry = chapter_registry if chapter_registry is not None else _make_chapter_registry()
    world.character_registry = character_registry if character_registry is not None else _make_character_registry()
    return world


# =============================================================================
# Helper function tests
# =============================================================================


class TestSanitizeRegionId:
    def test_chinese_name(self):
        assert _sanitize_region_id("边境地区") == "region_边境地区"

    def test_whitespace(self):
        assert _sanitize_region_id("Dark  Forest") == "region_Dark_Forest"

    def test_strip(self):
        assert _sanitize_region_id("  frontier  ") == "region_frontier"


class TestExtractCharField:
    def test_nested_path(self):
        data = {"profile": {"metadata": {"tier": "main"}}}
        assert _extract_char_field(data, "tier") == "main"

    def test_flat_path(self):
        data = {"tier": "secondary"}
        assert _extract_char_field(data, "tier") == "secondary"

    def test_nested_takes_priority(self):
        data = {"profile": {"metadata": {"tier": "main"}}, "tier": "secondary"}
        assert _extract_char_field(data, "tier") == "main"

    def test_default(self):
        assert _extract_char_field({}, "tier", "passerby") == "passerby"


class TestEventToBehaviors:
    def test_with_conditions(self):
        event = StoryEvent(
            id="evt_1",
            name="Test Event",
            trigger_conditions=ConditionGroup(
                operator="and",
                conditions=[
                    Condition(type=ConditionType.LOCATION, params={"area_id": "a"}),
                ],
            ),
            completion_conditions=ConditionGroup(
                operator="and",
                conditions=[
                    Condition(type=ConditionType.NPC_INTERACTED, params={"npc_id": "n"}),
                ],
            ),
            on_complete={"unlock_events": ["evt_2"], "add_xp": 100},
        )
        behaviors = _event_to_behaviors(event, "evt_1", "ch_01")
        assert len(behaviors) == 2
        assert behaviors[0].id == "bh_unlock_evt_1"
        assert behaviors[0].once is True
        assert behaviors[0].conditions is not None
        assert behaviors[1].id == "bh_complete_evt_1"
        # complete behavior should have: CHANGE_STATE + unlock_events EMIT + xp EMIT
        assert len(behaviors[1].actions) == 3

    def test_empty_conditions_creates_guarded_unlock(self):
        """空 trigger_conditions → 仍有状态守卫 (EVENT_STATE: status==LOCKED)。"""
        event = StoryEvent(
            id="evt_2",
            name="Always True",
            trigger_conditions=ConditionGroup(operator="and", conditions=[]),
        )
        behaviors = _event_to_behaviors(event, "evt_2", "ch_01")
        assert len(behaviors) == 1
        # 6a: 即使空条件，也有状态守卫
        assert behaviors[0].conditions is not None
        assert behaviors[0].conditions.conditions[0].type == ConditionType.EVENT_STATE

    def test_no_completion_conditions(self):
        event = StoryEvent(
            id="evt_3",
            name="No Complete",
            trigger_conditions=ConditionGroup(
                operator="and",
                conditions=[
                    Condition(type=ConditionType.LOCATION, params={"area_id": "a"}),
                ],
            ),
            completion_conditions=None,
        )
        behaviors = _event_to_behaviors(event, "evt_3", "ch_01")
        assert len(behaviors) == 1  # only unlock, no complete


# =============================================================================
# GraphBuilder tests
# =============================================================================


class TestBuildWorldRoot:
    def test_world_root_exists(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        node = wg.get_node("world_root")
        assert node is not None
        assert node.type == WorldNodeType.WORLD
        assert node.name == "Test World"
        assert node.properties["setting"] == "Fantasy"


class TestBuildChapters:
    def test_chapter_nodes(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        ch1 = wg.get_node("ch_01")
        ch2 = wg.get_node("ch_02")
        assert ch1 is not None
        assert ch2 is not None
        assert ch1.type == WorldNodeType.CHAPTER
        assert ch1.state["status"] == "active"
        assert ch2.state["status"] == "locked"

    def test_gate_edge(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        gate = wg.get_edge("ch_01", "ch_02", "gate_ch_01_ch_02")
        assert gate is not None
        assert gate["relation"] == WorldEdgeType.GATE.value

    def test_unlock_behavior_on_target(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        ch2 = wg.get_node("ch_02")
        assert len(ch2.behaviors) == 1
        bh = ch2.behaviors[0]
        assert bh.id == "bh_gate_ch_01_ch_02"
        assert bh.once is True
        assert bh.conditions is not None

    def test_contains_world_root_to_chapter(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        children = wg.get_children("world_root", WorldNodeType.CHAPTER)
        assert "ch_01" in children
        assert "ch_02" in children


class TestBuildRegions:
    def test_regions_auto_aggregated(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        regions = wg.get_by_type(WorldNodeType.REGION)
        assert len(regions) == 2  # Frontier + Underground

    def test_same_region_grouped(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        frontier_id = _sanitize_region_id("Frontier")
        children = wg.get_children(frontier_id)
        assert "town_square" in children
        assert "dark_forest" in children
        assert "goblin_cave" not in children

    def test_region_contains_world_root(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        frontier_id = _sanitize_region_id("Frontier")
        parent = wg.get_parent(frontier_id)
        assert parent == "world_root"


class TestBuildAreasAndLocations:
    def test_area_nodes(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        areas = wg.get_by_type(WorldNodeType.AREA)
        assert len(areas) == 3

        town = wg.get_node("town_square")
        assert town is not None
        assert town.state["visited"] is False
        assert town.state["visit_count"] == 0

    def test_location_nodes(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        locations = wg.get_by_type(WorldNodeType.LOCATION)
        assert len(locations) == 2  # tavern + blacksmith

        tavern = wg.get_node("loc_town_square_tavern")
        assert tavern is not None
        assert tavern.name == "The Rusty Goblet"

    def test_contains_hierarchy(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        # area → location
        children = wg.get_children("town_square", WorldNodeType.LOCATION)
        assert "loc_town_square_tavern" in children
        assert "loc_town_square_blacksmith" in children

    def test_connects_edges(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        connected = wg.get_connected_areas("town_square")
        assert "dark_forest" in connected

        # Reverse edge should also exist
        connected_rev = wg.get_connected_areas("dark_forest")
        assert "town_square" in connected_rev


class TestBuildEvents:
    def test_event_nodes(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        events = wg.get_by_type(WorldNodeType.EVENT_DEF)
        assert len(events) == 2

        evt = wg.get_node("evt_meet_npc")
        assert evt is not None
        assert evt.state["status"] == EventStatus.LOCKED
        assert evt.properties["chapter_id"] == "ch_01"
        assert evt.properties["is_required"] is True

    def test_has_event_edge(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        # ch_01 available_maps[0] = "town_square"
        entities = wg.get_entities_at("town_square")
        assert "evt_meet_npc" in entities
        assert "evt_forest_quest" in entities

    def test_event_behaviors(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        evt = wg.get_node("evt_meet_npc")
        # Should have unlock + complete behaviors
        assert len(evt.behaviors) == 2

        evt2 = wg.get_node("evt_forest_quest")
        # Empty conditions → only unlock behavior, no complete
        assert len(evt2.behaviors) == 1
        # 6a: 状态守卫 — 即使空条件也有 EVENT_STATE guard
        assert evt2.behaviors[0].conditions is not None

    def test_complete_behavior_actions(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        evt = wg.get_node("evt_meet_npc")
        complete_bh = [b for b in evt.behaviors if b.id.startswith("bh_complete_")]
        assert len(complete_bh) == 1
        # CHANGE_STATE + unlock_events EMIT + xp EMIT
        assert len(complete_bh[0].actions) == 3


class TestBuildCharacters:
    def test_npc_nodes(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        npcs = wg.get_by_type(WorldNodeType.NPC)
        assert len(npcs) == 3

        smith = wg.get_node("npc_blacksmith")
        assert smith is not None
        assert smith.name == "Grom"
        assert smith.properties["tier"] == "secondary"

    def test_hosts_edge_with_sublocation(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        # npc_blacksmith → loc_town_square_blacksmith
        entities = wg.get_entities_at("loc_town_square_blacksmith")
        assert "npc_blacksmith" in entities

    def test_hosts_edge_area_only(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        # npc_hermit → dark_forest (no sub_location)
        entities = wg.get_entities_at("dark_forest")
        assert "npc_hermit" in entities

    def test_field_extraction_compat(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        # npc_hermit uses flat format
        hermit = wg.get_node("npc_hermit")
        assert hermit.name == "Old Man"
        assert hermit.properties["tier"] == "passerby"


class TestBuildRelationships:
    def test_relates_to_edge(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session, use_canonical_relationships=True)

        edges = wg.get_neighbors("npc_blacksmith", WorldEdgeType.RELATES_TO.value)
        assert len(edges) == 1
        target_id, edge_data = edges[0]
        assert target_id == "npc_innkeeper"
        assert edge_data["relationship_type"] == "friend"

    def test_no_relationships_when_disabled(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session, use_canonical_relationships=False)

        edges = wg.get_neighbors("npc_blacksmith", WorldEdgeType.RELATES_TO.value)
        assert len(edges) == 0


class TestBuildParty:
    def test_camp_node(self):
        world = _make_world()
        session = _make_session(with_party=True)
        wg = GraphBuilder.build(world, session)

        camp = wg.get_node("camp")
        assert camp is not None
        assert camp.type == WorldNodeType.CAMP

        # CONTAINS: world_root → camp
        children = wg.get_children("world_root", WorldNodeType.CAMP)
        assert "camp" in children

    def test_member_of_edges(self):
        world = _make_world()
        session = _make_session(with_party=True)
        wg = GraphBuilder.build(world, session)

        # Active members: npc_innkeeper + npc_blacksmith (npc_ghost is inactive)
        innkeeper_edges = wg.get_neighbors(
            "npc_innkeeper", WorldEdgeType.MEMBER_OF.value
        )
        assert len(innkeeper_edges) == 1
        assert innkeeper_edges[0][0] == "camp"

        smith_edges = wg.get_neighbors(
            "npc_blacksmith", WorldEdgeType.MEMBER_OF.value
        )
        assert len(smith_edges) == 1
        assert smith_edges[0][0] == "camp"

    def test_inactive_member_skipped(self):
        world = _make_world()
        session = _make_session(with_party=True)
        wg = GraphBuilder.build(world, session)

        # npc_ghost is inactive, should not have MEMBER_OF edge
        assert not wg.has_node("npc_ghost")  # not in character_registry

    def test_no_party(self):
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        camp = wg.get_node("camp")
        assert camp is not None
        # No MEMBER_OF edges
        all_member_of = [
            (s, t)
            for s, t, d in wg.graph.edges(data=True)
            if d.get("relation") == WorldEdgeType.MEMBER_OF.value
        ]
        assert len(all_member_of) == 0


class TestFullBuild:
    def test_full_build_stats(self):
        world = _make_world()
        session = _make_session(with_party=True)
        wg = GraphBuilder.build(world, session)

        stats = wg.stats()
        assert stats["node_count"] > 0
        assert stats["edge_count"] > 0

        # Expected nodes:
        #   1 world_root
        #   2 chapters (ch_01, ch_02)
        #   2 regions (Frontier, Underground)
        #   3 areas (town_square, dark_forest, goblin_cave)
        #   2 locations (tavern, blacksmith)
        #   2 events (evt_meet_npc, evt_forest_quest)
        #   3 NPCs (blacksmith, innkeeper, hermit)
        #   1 camp
        # = 16
        assert stats["node_count"] == 16

        # Type distribution should cover all types
        dist = stats["type_distribution"]
        assert dist.get(WorldNodeType.WORLD) == 1
        assert dist.get(WorldNodeType.CHAPTER) == 2
        assert dist.get(WorldNodeType.REGION) == 2
        assert dist.get(WorldNodeType.AREA) == 3
        assert dist.get(WorldNodeType.LOCATION) == 2
        assert dist.get(WorldNodeType.EVENT_DEF) == 2
        assert dist.get(WorldNodeType.NPC) == 3
        assert dist.get(WorldNodeType.CAMP) == 1

    def test_full_build_connectivity(self):
        """验证图的基本连通性：world_root 可以通过 CONTAINS 到达所有地理节点。"""
        world = _make_world()
        session = _make_session(with_party=True)
        wg = GraphBuilder.build(world, session)

        # All regions reachable from world_root
        descendants = wg.get_descendants("world_root")
        region_ids = wg.get_by_type(WorldNodeType.REGION)
        for rid in region_ids:
            assert rid in descendants

        # All areas reachable from world_root
        area_ids = wg.get_by_type(WorldNodeType.AREA)
        for aid in area_ids:
            assert aid in descendants

        # All locations reachable from world_root
        loc_ids = wg.get_by_type(WorldNodeType.LOCATION)
        for lid in loc_ids:
            assert lid in descendants

    def test_scope_chain(self):
        """验证 location 的作用域链完整。"""
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        chain = wg.get_scope_chain("loc_town_square_tavern")
        # [loc, area, region, world_root]
        assert chain[0] == "loc_town_square_tavern"
        assert chain[1] == "town_square"
        assert chain[2] == _sanitize_region_id("Frontier")
        assert chain[3] == "world_root"


# =============================================================================
# Codex 审查补充测试
# =============================================================================


class TestDirtyDataResilience:
    """Codex 审查发现: on_complete 字段为 None 时迭代崩溃。"""

    def test_on_complete_none_values(self):
        """on_complete 的 unlock_events/add_items 为 None 时不应崩溃。"""
        event = StoryEvent(
            id="evt_dirty",
            name="Dirty Data",
            trigger_conditions=ConditionGroup(
                operator="and",
                conditions=[
                    Condition(type=ConditionType.LOCATION, params={"area_id": "a"}),
                ],
            ),
            completion_conditions=ConditionGroup(
                operator="and",
                conditions=[
                    Condition(type=ConditionType.NPC_INTERACTED, params={"npc_id": "n"}),
                ],
            ),
            on_complete={"unlock_events": None, "add_items": None, "add_xp": None},
        )
        behaviors = _event_to_behaviors(event, "evt_dirty", "ch_01")
        assert len(behaviors) == 2
        # complete behavior should only have CHANGE_STATE (no EMIT since all None)
        assert len(behaviors[1].actions) == 1


class TestConnectsDedup:
    """Codex 审查发现: 双端配置 connections 时可能产生重复边。"""

    def test_bidirectional_connections_no_duplicate(self):
        """A→B 和 B→A 都配了 connection，不应产生重复边。"""
        world = _make_world()
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        # town_square ↔ dark_forest: 两端都配了 connection
        edges_forward = wg.get_edges_between("town_square", "dark_forest")
        edges_reverse = wg.get_edges_between("dark_forest", "town_square")

        # 应只有 1 条正向 + 1 条自动反向 = 每个方向 1 条
        connects_forward = [
            (k, d) for k, d in edges_forward
            if d.get("relation") == WorldEdgeType.CONNECTS.value
        ]
        connects_reverse = [
            (k, d) for k, d in edges_reverse
            if d.get("relation") == WorldEdgeType.CONNECTS.value
        ]
        assert len(connects_forward) == 1
        assert len(connects_reverse) == 1


class TestNpcFallbackStateConsistency:
    """Codex 审查发现: 子地点回退时 state.current_location 不一致。"""

    def test_fallback_corrects_state(self):
        """NPC 子地点不存在回退到 area 时，state 应同步修正。"""
        char_registry = {
            "npc_orphan": {
                "id": "npc_orphan",
                "name": "Orphan NPC",
                "profile": {
                    "name": "Orphan",
                    "metadata": {
                        "default_map": "town_square",
                        "default_sub_location": "nonexistent_place",
                        "tier": "secondary",
                    },
                },
            },
        }
        world = _make_world(character_registry=char_registry)
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)

        npc = wg.get_node("npc_orphan")
        assert npc is not None
        # state should be corrected to area, not the invalid location
        assert npc.state["current_location"] == "town_square"
        # HOSTS edge should point from area
        entities = wg.get_entities_at("town_square")
        assert "npc_orphan" in entities


class TestRelationshipsDictFormat:
    """Codex 审查发现: dict 形态的 relationships 未测。"""

    def test_dict_relationships(self):
        """relationships 为 dict 格式时也应正确构建 RELATES_TO 边。"""
        char_registry = {
            "npc_a": {
                "id": "npc_a",
                "name": "NPC A",
                "default_map": "town_square",
                "profile": {
                    "name": "A",
                    "relationships": {
                        "npc_b": {"type": "rival", "description": "Old rival."},
                    },
                },
            },
            "npc_b": {
                "id": "npc_b",
                "name": "NPC B",
                "default_map": "town_square",
                "profile": {"name": "B"},
            },
        }
        world = _make_world(character_registry=char_registry)
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session, use_canonical_relationships=True)

        edges = wg.get_neighbors("npc_a", WorldEdgeType.RELATES_TO.value)
        assert len(edges) == 1
        target_id, edge_data = edges[0]
        assert target_id == "npc_b"
        assert edge_data["relationship_type"] == "rival"

    def test_string_relationships(self):
        """relationships 为 dict[str, str] 格式也应工作。"""
        char_registry = {
            "npc_x": {
                "id": "npc_x",
                "name": "NPC X",
                "default_map": "town_square",
                "profile": {
                    "name": "X",
                    "relationships": {
                        "npc_y": "mentor",
                    },
                },
            },
            "npc_y": {
                "id": "npc_y",
                "name": "NPC Y",
                "default_map": "town_square",
                "profile": {"name": "Y"},
            },
        }
        world = _make_world(character_registry=char_registry)
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session, use_canonical_relationships=True)

        edges = wg.get_neighbors("npc_x", WorldEdgeType.RELATES_TO.value)
        assert len(edges) == 1


# =============================================================================
# E4/U8: activation_type 行为生成测试
# =============================================================================


def _make_story_event(
    event_id: str,
    name: str = "Test Event",
    activation_type: str = "event_driven",
    time_limit=None,
    is_repeatable: bool = False,
    cooldown_rounds: int = 0,
    discovery_check=None,
    trigger_conditions=None,
    completion_conditions=None,
) -> StoryEvent:
    """快捷构建 StoryEvent 用于测试 _event_to_behaviors。"""
    kwargs = {
        "id": event_id,
        "name": name,
        "activation_type": activation_type,
        "is_repeatable": is_repeatable,
        "cooldown_rounds": cooldown_rounds,
    }
    if time_limit is not None:
        kwargs["time_limit"] = time_limit
    if discovery_check is not None:
        kwargs["discovery_check"] = discovery_check
    if trigger_conditions is not None:
        kwargs["trigger_conditions"] = trigger_conditions
    if completion_conditions is not None:
        kwargs["completion_conditions"] = completion_conditions
    return StoryEvent(**kwargs)


class TestActivationTypeBehaviors:
    """U8: activation_type 行为生成。"""

    def test_npc_given_no_unlock_behavior(self):
        """npc_given 事件不生成 unlock behavior。"""
        from app.world.models import TriggerType
        event = _make_story_event("evt_npc", activation_type="npc_given")
        behaviors = _event_to_behaviors(event, "evt_npc", "ch_01")
        # 没有任何 bh_unlock_ 开头的 behavior
        unlock_bhs = [b for b in behaviors if b.id.startswith("bh_unlock_")]
        assert len(unlock_bhs) == 0

    def test_auto_enter_on_enter_trigger(self):
        """auto_enter 事件生成 ON_ENTER trigger 的 unlock behavior。"""
        from app.world.models import TriggerType
        event = _make_story_event("evt_enter", activation_type="auto_enter")
        behaviors = _event_to_behaviors(event, "evt_enter", "ch_01")
        unlock_bh = next((b for b in behaviors if b.id == "bh_unlock_evt_enter"), None)
        assert unlock_bh is not None
        assert unlock_bh.trigger == TriggerType.ON_ENTER
        assert unlock_bh.once is True
        assert unlock_bh.priority == 10
        # 只有 unlock_guard 条件
        assert unlock_bh.conditions is not None
        conds = unlock_bh.conditions.conditions
        assert len(conds) == 1
        assert conds[0].params["key"] == "status"
        assert conds[0].params["value"] == EventStatus.LOCKED

    def test_discovery_on_enter_with_narrative_hint(self):
        """discovery 事件生成 ON_ENTER + NARRATIVE_HINT action。"""
        from app.world.models import ActionType, TriggerType
        discovery_check = {"skill": "感知", "dc": 15}
        event = _make_story_event("evt_disc", activation_type="discovery", discovery_check=discovery_check)
        behaviors = _event_to_behaviors(event, "evt_disc", "ch_01")
        unlock_bh = next((b for b in behaviors if b.id == "bh_unlock_evt_disc"), None)
        assert unlock_bh is not None
        assert unlock_bh.trigger == TriggerType.ON_ENTER
        # 应有两个 actions: CHANGE_STATE + NARRATIVE_HINT
        action_types = [a.type for a in unlock_bh.actions]
        assert ActionType.CHANGE_STATE in action_types
        assert ActionType.NARRATIVE_HINT in action_types
        hint_action = next(a for a in unlock_bh.actions if a.type == ActionType.NARRATIVE_HINT)
        assert "感知" in hint_action.params.get("text", "")
        assert "15" in hint_action.params.get("text", "")

    def test_discovery_default_check_values(self):
        """discovery 事件无 discovery_check 时使用默认值。"""
        from app.world.models import ActionType, TriggerType
        event = _make_story_event("evt_disc2", activation_type="discovery")
        behaviors = _event_to_behaviors(event, "evt_disc2", "ch_01")
        unlock_bh = next((b for b in behaviors if b.id == "bh_unlock_evt_disc2"), None)
        assert unlock_bh is not None
        hint_action = next((a for a in unlock_bh.actions if a.type == ActionType.NARRATIVE_HINT), None)
        assert hint_action is not None
        assert "感知" in hint_action.params.get("text", "")  # 默认 skill
        assert "15" in hint_action.params.get("text", "")   # 默认 dc

    def test_event_driven_on_tick_trigger(self):
        """event_driven 事件保持 ON_TICK trigger（临时策略）。"""
        from app.world.models import TriggerType
        event = _make_story_event("evt_driven", activation_type="event_driven")
        behaviors = _event_to_behaviors(event, "evt_driven", "ch_01")
        unlock_bh = next((b for b in behaviors if b.id == "bh_unlock_evt_driven"), None)
        assert unlock_bh is not None
        assert unlock_bh.trigger == TriggerType.ON_TICK

    def test_default_activation_type_on_tick(self):
        """activation_type 为空/默认时保持 ON_TICK trigger。"""
        from app.world.models import TriggerType
        event = StoryEvent(id="evt_def", name="Default Event")  # activation_type 默认 "event_driven"
        behaviors = _event_to_behaviors(event, "evt_def", "ch_01")
        unlock_bh = next((b for b in behaviors if b.id == "bh_unlock_evt_def"), None)
        assert unlock_bh is not None
        assert unlock_bh.trigger == TriggerType.ON_TICK


class TestTimeoutBehavior:
    """U9: 超时 behavior 生成。"""

    def test_time_limit_generates_timeout_behavior(self):
        """有 time_limit 的事件生成 bh_timeout_ 行为。"""
        from app.models.narrative import ConditionType
        from app.world.models import ActionType, TriggerType
        event = _make_story_event("evt_timed", time_limit=5)
        behaviors = _event_to_behaviors(event, "evt_timed", "ch_01")
        timeout_bh = next((b for b in behaviors if b.id == "bh_timeout_evt_timed"), None)
        assert timeout_bh is not None
        assert timeout_bh.trigger == TriggerType.ON_TICK
        assert timeout_bh.once is True
        assert timeout_bh.priority == 3
        # 验证条件：status==ACTIVE + event_rounds_elapsed
        cond_types = [c.type for c in timeout_bh.conditions.conditions]
        assert ConditionType.EVENT_STATE in cond_types
        assert ConditionType.EVENT_ROUNDS_ELAPSED in cond_types
        # min_rounds 等于 time_limit
        rounds_cond = next(c for c in timeout_bh.conditions.conditions if c.type == ConditionType.EVENT_ROUNDS_ELAPSED)
        assert rounds_cond.params["min_rounds"] == 5
        # action: FAILED
        assert len(timeout_bh.actions) == 1
        action = timeout_bh.actions[0]
        assert action.type == ActionType.CHANGE_STATE
        assert action.params["updates"]["status"] == EventStatus.FAILED
        assert action.params["updates"]["failure_reason"] == "timeout"

    def test_no_time_limit_no_timeout_behavior(self):
        """无 time_limit 的事件不生成 bh_timeout_ 行为。"""
        event = _make_story_event("evt_notimed")
        behaviors = _event_to_behaviors(event, "evt_notimed", "ch_01")
        timeout_bhs = [b for b in behaviors if b.id.startswith("bh_timeout_")]
        assert len(timeout_bhs) == 0

    def test_timeout_priority_lower_than_complete(self):
        """超时 priority(3) < complete priority(5)，确保完成优先。"""
        from app.world.models import TriggerType
        event = _make_story_event(
            "evt_both",
            time_limit=3,
            completion_conditions=ConditionGroup(operator="and", conditions=[
                Condition(type=ConditionType.ROUNDS_ELAPSED, params={"min_rounds": 1}),
            ]),
        )
        behaviors = _event_to_behaviors(event, "evt_both", "ch_01")
        timeout_bh = next((b for b in behaviors if b.id == "bh_timeout_evt_both"), None)
        complete_bh = next((b for b in behaviors if b.id == "bh_complete_evt_both"), None)
        assert timeout_bh is not None and complete_bh is not None
        assert complete_bh.priority > timeout_bh.priority


class TestCooldownBehavior:
    """U9: 冷却 behavior 生成。"""

    def test_repeatable_with_cooldown_generates_cooldown_behavior(self):
        """is_repeatable + cooldown_rounds > 0 生成 bh_cooldown_ 行为。"""
        from app.models.narrative import ConditionType
        from app.world.models import ActionType, TriggerType
        event = _make_story_event("evt_rep", is_repeatable=True, cooldown_rounds=3)
        behaviors = _event_to_behaviors(event, "evt_rep", "ch_01")
        cooldown_bh = next((b for b in behaviors if b.id == "bh_cooldown_evt_rep"), None)
        assert cooldown_bh is not None
        assert cooldown_bh.trigger == TriggerType.ON_TICK
        assert cooldown_bh.once is False
        assert cooldown_bh.priority == 2
        # 验证条件：status==COOLDOWN + event_rounds_elapsed
        cond_types = [c.type for c in cooldown_bh.conditions.conditions]
        assert ConditionType.EVENT_STATE in cond_types
        assert ConditionType.EVENT_ROUNDS_ELAPSED in cond_types
        status_cond = next(c for c in cooldown_bh.conditions.conditions if c.type == ConditionType.EVENT_STATE)
        assert status_cond.params["value"] == EventStatus.COOLDOWN
        # min_rounds 等于 cooldown_rounds
        rounds_cond = next(c for c in cooldown_bh.conditions.conditions if c.type == ConditionType.EVENT_ROUNDS_ELAPSED)
        assert rounds_cond.params["min_rounds"] == 3
        # action: 回 AVAILABLE + 重置各字段
        action = cooldown_bh.actions[0]
        assert action.params["updates"]["status"] == EventStatus.AVAILABLE
        assert action.params["updates"]["failure_reason"] is None

    def test_non_repeatable_no_cooldown_behavior(self):
        """非 is_repeatable 事件不生成 bh_cooldown_ 行为。"""
        event = _make_story_event("evt_norep", is_repeatable=False, cooldown_rounds=3)
        behaviors = _event_to_behaviors(event, "evt_norep", "ch_01")
        cooldown_bhs = [b for b in behaviors if b.id.startswith("bh_cooldown_")]
        assert len(cooldown_bhs) == 0

    def test_repeatable_zero_cooldown_no_cooldown_behavior(self):
        """cooldown_rounds=0 的 is_repeatable 事件不生成 bh_cooldown_ 行为（统一由 session_runtime 处理）。"""
        event = _make_story_event("evt_zero", is_repeatable=True, cooldown_rounds=0)
        behaviors = _event_to_behaviors(event, "evt_zero", "ch_01")
        cooldown_bhs = [b for b in behaviors if b.id.startswith("bh_cooldown_")]
        assert len(cooldown_bhs) == 0

    def test_failure_reason_in_evt_state(self):
        """evt_state 包含 failure_reason 字段（初始化为 None）。"""
        world = _make_world(chapter_registry={
            "ch_01": {
                "id": "ch_01",
                "name": "Test",
                "mainline_id": "main",
                "status": "active",
                "available_maps": ["town_square"],
                "events": [{
                    "id": "evt_fail_state",
                    "name": "Fail State Event",
                    "time_limit": 5,
                }],
            }
        })
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)
        node = wg.get_node("evt_fail_state")
        assert node is not None
        assert "failure_reason" in node.state
        assert node.state["failure_reason"] is None

    def test_cooldown_rounds_in_evt_props(self):
        """cooldown_rounds 字段被正确写入 evt_props。"""
        world = _make_world(chapter_registry={
            "ch_01": {
                "id": "ch_01",
                "name": "Test",
                "mainline_id": "main",
                "status": "active",
                "available_maps": ["town_square"],
                "events": [{
                    "id": "evt_cool_prop",
                    "name": "Cooldown Props Event",
                    "is_repeatable": True,
                    "cooldown_rounds": 7,
                }],
            }
        })
        session = _make_session(with_party=False)
        wg = GraphBuilder.build(world, session)
        node = wg.get_node("evt_cool_prop")
        assert node is not None
        assert node.properties.get("cooldown_rounds") == 7
