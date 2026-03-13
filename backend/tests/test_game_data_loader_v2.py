"""V2 数据加载器测试 — 需要 v2 JSON 文件存在才运行。"""
from __future__ import annotations

import asyncio
import pytest
from pathlib import Path

from app.game_data_loader_v2 import _V2_DIR, load_v2_world_data

pytestmark = pytest.mark.skipif(
    not (_V2_DIR / "characters.json").exists(),
    reason="V2 data not generated yet — run app.v2_data_pipeline first",
)


def test_v2_loads_without_errors() -> None:
    from app.game_core.bootstrap import build_default_world
    world_data = load_v2_world_data()
    world = build_default_world("goblin_slayer", world_data=world_data)
    assert world is not None


def test_v2_all_registries_populated() -> None:
    from app.game_core.bootstrap import build_default_world
    world_data = load_v2_world_data()
    world = build_default_world("goblin_slayer", world_data=world_data)
    for name in [
        "maps",
        "battle_maps",
        "classes",
        "skills",
        "lore",
        "characters",
        "items",
        "monsters",
        "factions",
        "quests",
    ]:
        registry = world.get_registry(name)
        assert registry is not None, f"{name} registry not found"
        assert registry.list_all(), f"{name} registry is empty"


def test_v2_cross_registry_references() -> None:
    from app.game_core.bootstrap import build_default_world
    world_data = load_v2_world_data()
    world = build_default_world("goblin_slayer", world_data=world_data)
    issues = world.validate()
    critical = [i for i in issues if "not found" in str(i).lower()]
    assert not critical, f"Critical reference issues: {critical}"


def test_v2_knowledge_graph_edges() -> None:
    from app.game_core.bootstrap import build_default_world
    from app.world_knowledge_graph import WorldKnowledgeGraph
    world_data = load_v2_world_data()
    world = build_default_world("goblin_slayer", world_data=world_data)
    graph = WorldKnowledgeGraph()
    graph.ensure_seeded(world)
    assert graph._graph.number_of_nodes() > 20, "Graph has too few nodes"
    assert graph._graph.number_of_edges() > 10, "Graph has too few edges"


def test_v2_frontier_town_content_ring_contains_new_items_npcs_and_sublocations() -> None:
    world_data = load_v2_world_data()

    items = world_data["items"]
    characters = world_data["characters"]
    maps = world_data["maps"]

    assert {
        "trail_rations",
        "waterskin",
        "bandage_roll",
        "lamp_oil_flask",
        "chalk_bundle",
        "travel_bedroll",
        "climbing_hook",
        "blessed_salt_packet",
        "herbal_repellent",
        "quilted_gambeson",
    }.issubset(items.keys())

    assert {
        "guild_quartermaster",
        "tavern_keeper",
        "temple_matron",
        "north_gate_warden",
    }.issubset(characters.keys())

    frontier_town = maps["frontier_town"]
    ancient_ruins = maps["ancient_ruins"]
    assert "north_gate" in frontier_town["sub_locations"]
    assert "forest_approach" in ancient_ruins["sub_locations"]
    assert "waystone_clearing" in ancient_ruins["sub_locations"]


def test_v2_ancient_ruins_discoveries_reference_expanded_frontier_items() -> None:
    world_data = load_v2_world_data()
    ancient_ruins = world_data["maps"]["ancient_ruins"]
    discovery_rewards = {
        entry["id"]: set(entry.get("reward", {}).get("items", []))
        for entry in ancient_ruins.get("discoveries", [])
    }

    assert discovery_rewards["hunter_stash"] == {"bandage_roll", "trail_rations"}
    assert discovery_rewards["waystone_charms"] == {"blessed_salt_packet"}


def test_v2_key_npc_schedules_include_room_targets() -> None:
    characters = load_v2_world_data()["characters"]

    assert characters["goblin_slayer"]["schedule"]["dusk"]["room"] == "storage_area"
    assert characters["goblin_slayer"]["schedule"]["night"]["room"] == "workbench"
    assert characters["high_elf_archer"]["schedule"]["dawn"]["room"] == "sparring_ring"
    assert characters["high_elf_archer"]["schedule"]["night"]["room"] == "main_hall"
    assert characters["dwarf_shaman"]["schedule"]["day"]["room"] == "main_hall"
    assert characters["lizard_priest"]["schedule"]["dawn"]["room"] == "prayer_hall"
    assert characters["priestess"]["schedule"]["day"]["room"] == "herb_garden"
    assert characters["guild_girl"]["schedule"]["day"]["room"] == "guild_counter"
    assert characters["guild_quartermaster"]["schedule"]["night"]["room"] == "main_hall"


def test_v2_schedule_hook_places_key_npcs_in_expected_rooms() -> None:
    from app.game_core.bootstrap import build_default_world
    from app.game_core.orchestration.hooks.npc_schedule import NpcScheduleHook
    from app.game_core.orchestration.scene_bus import SceneBus
    from app.game_core.orchestration.settlement import SettlementContext
    from app.game_core.rules import RulesEngine
    from app.game_core.rules.handlers.world_state import WorldStateHandler
    from app.game_core.state import StateContainer

    world = build_default_world("goblin_slayer", world_data=load_v2_world_data())
    state = StateContainer.create_new(world)
    engine = RulesEngine()
    engine.register(WorldStateHandler())

    def _apply_delta(delta) -> None:
        if delta is not None:
            state.apply(delta)

    def _run_schedule(slot: int) -> None:
        state.time.restore({"day": 1, "slot": slot})
        context = SettlementContext(
            change_log=[],
            state=state,
            world=world,
            scene_bus=SceneBus(state.scene),
            _rules_engine=engine,
            _apply_delta=_apply_delta,
        )
        asyncio.run(NpcScheduleHook().execute(context))

    _run_schedule(17)
    assert state.areas.get_area("cow_girl_farm").npc_rooms["goblin_slayer"] == "storage_area"
    assert state.areas.get_area("frontier_town").npc_rooms["dwarf_shaman"] == "main_hall"
    assert state.areas.get_area("frontier_town").npc_rooms["priestess"] == "guild_hall"

    _run_schedule(19)
    assert state.areas.get_area("cow_girl_farm").npc_rooms["goblin_slayer"] == "workbench"
    assert state.areas.get_area("frontier_town").npc_rooms["high_elf_archer"] == "main_hall"
    assert state.areas.get_area("frontier_town").npc_rooms["guild_quartermaster"] == "main_hall"
