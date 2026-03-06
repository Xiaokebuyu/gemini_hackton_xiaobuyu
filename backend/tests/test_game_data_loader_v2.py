"""V2 数据加载器测试 — 需要 v2 JSON 文件存在才运行。"""
from __future__ import annotations

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
    for name in ["maps", "classes", "skills", "lore", "characters", "items", "monsters", "factions", "quests"]:
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
