#!/usr/bin/env python3
"""
GM Natural Language CLI - 测试GM自然语言事件摄入和视角分发

用法:
    # 简单事件摄入
    python -m app.tools.gm_natural_cli ingest <world_id> <game_day> "<event_description>"

    # 带角色位置的事件摄入
    python -m app.tools.gm_natural_cli ingest-with-locations <world_id> <game_day> "<event_description>"

    # 查看GM图谱
    python -m app.tools.gm_natural_cli show-gm <world_id>

    # 查看角色图谱
    python -m app.tools.gm_natural_cli show-char <world_id> <character_id>

示例:
    # 战斗事件
    python -m app.tools.gm_natural_cli ingest test_world 5 "在集市广场，Marcus与强盗发生了战斗。Gorn在一旁目睹了整个过程，而Elena在远处的铁匠铺听到了喧闹声。"

    # 简单对话事件
    python -m app.tools.gm_natural_cli ingest test_world 6 "Elena向Marcus询问了昨天战斗的情况。"
"""
import asyncio
import json
import sys
from typing import Dict, List

from app.models.event import NaturalEventIngestRequest
from app.services.gm_flash_service import GMFlashService
from app.services.graph_store import GraphStore


async def ingest_event(
    world_id: str,
    game_day: int,
    event_description: str,
    known_characters: List[str] = None,
    known_locations: List[str] = None,
    character_locations: Dict[str, str] = None,
):
    """摄入自然语言事件"""
    gm_service = GMFlashService()

    print(f"\n{'='*60}")
    print(f"GM自然语言事件摄入测试")
    print(f"{'='*60}")
    print(f"世界: {world_id}")
    print(f"游戏日: Day {game_day}")
    print(f"\n事件描述:")
    print(f"  {event_description}")

    if known_characters:
        print(f"\n已知角色: {', '.join(known_characters)}")
    if known_locations:
        print(f"已知地点: {', '.join(known_locations)}")
    if character_locations:
        print(f"角色位置: {character_locations}")

    print(f"\n{'-'*60}")
    print("正在处理...")

    request = NaturalEventIngestRequest(
        event_description=event_description,
        game_day=game_day,
        known_characters=known_characters or [],
        known_locations=known_locations or [],
        character_locations=character_locations or {},
        distribute=True,
        write_indexes=False,
    )

    result = await gm_service.ingest_event_natural(world_id, request)

    print(f"\n{'='*60}")
    print("摄入结果")
    print(f"{'='*60}")

    print(f"\n事件ID: {result.event_id}")

    print(f"\n解析结果:")
    print(f"  类型: {result.parsed_event.get('event_type', 'unknown')}")
    print(f"  摘要: {result.parsed_event.get('summary', '')}")
    print(f"  地点: {result.parsed_event.get('location', '未知')}")
    print(f"  参与者: {result.parsed_event.get('participants', [])}")
    print(f"  目击者: {result.parsed_event.get('witnesses', [])}")
    print(f"  重要度: {result.parsed_event.get('importance', 0.5)}")

    print(f"\nGM图谱:")
    print(f"  节点数: {result.gm_node_count}")
    print(f"  边数: {result.gm_edge_count}")

    print(f"\n角色分发 (dispatched={result.dispatched}):")
    if result.recipients:
        for r in result.recipients:
            print(f"\n  {r.character_id} [{r.perspective}]:")
            print(f"    节点: {r.node_count}, 边: {r.edge_count}")
            if r.event_description:
                desc = r.event_description[:80] + "..." if len(r.event_description) > 80 else r.event_description
                print(f"    视角描述: {desc}")
    else:
        print("  （无接收者）")

    if result.note:
        print(f"\n备注: {result.note}")


async def ingest_with_locations(world_id: str, game_day: int, event_description: str):
    """带预设角色位置的事件摄入"""
    # 预设的角色和位置
    known_characters = ["marcus", "gorn", "elena", "thief"]
    known_locations = ["marketplace", "smithy", "tavern", "town_square"]
    character_locations = {
        "marcus": "marketplace",
        "gorn": "marketplace",
        "elena": "smithy",
    }

    await ingest_event(
        world_id=world_id,
        game_day=game_day,
        event_description=event_description,
        known_characters=known_characters,
        known_locations=known_locations,
        character_locations=character_locations,
    )


async def show_gm_graph(world_id: str):
    """显示GM图谱"""
    graph_store = GraphStore()
    graph_data = await graph_store.load_graph(world_id, "gm", character_id=None)

    print(f"\n{'='*60}")
    print(f"GM图谱 - {world_id}")
    print(f"{'='*60}")

    if not graph_data or not graph_data.nodes:
        print("（图谱为空）")
        return

    print(f"\n节点 ({len(graph_data.nodes)}):")
    for node in graph_data.nodes:
        print(f"\n  [{node.type}] {node.id}")
        print(f"    名称: {node.name}")
        print(f"    重要度: {node.importance}")
        if node.properties:
            for k, v in node.properties.items():
                if v is not None:
                    v_str = str(v)[:50] + "..." if len(str(v)) > 50 else str(v)
                    print(f"    {k}: {v_str}")

    print(f"\n边 ({len(graph_data.edges)}):")
    for edge in graph_data.edges:
        print(f"  {edge.source} --[{edge.relation}]--> {edge.target} (w={edge.weight})")


async def show_character_graph(world_id: str, character_id: str):
    """显示角色图谱"""
    graph_store = GraphStore()
    graph_data = await graph_store.load_graph(world_id, "character", character_id=character_id)

    print(f"\n{'='*60}")
    print(f"角色图谱 - {character_id} @ {world_id}")
    print(f"{'='*60}")

    # 获取profile
    profile = await graph_store.get_character_profile(world_id, character_id)
    if profile:
        print(f"\n角色资料:")
        print(f"  名字: {profile.get('name', '未知')}")
        print(f"  职业: {profile.get('occupation', '未知')}")

    if not graph_data or not graph_data.nodes:
        print("\n（图谱为空）")
        return

    print(f"\n节点 ({len(graph_data.nodes)}):")
    for node in graph_data.nodes:
        print(f"\n  [{node.type}] {node.id}")
        print(f"    名称: {node.name}")
        print(f"    重要度: {node.importance}")
        if node.properties:
            for k, v in node.properties.items():
                if v is not None:
                    v_str = str(v)[:60] + "..." if len(str(v)) > 60 else str(v)
                    print(f"    {k}: {v_str}")

    print(f"\n边 ({len(graph_data.edges)}):")
    for edge in graph_data.edges:
        print(f"  {edge.source} --[{edge.relation}]--> {edge.target} (w={edge.weight})")


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1]

    if command == "ingest":
        if len(sys.argv) < 5:
            print("用法: python -m app.tools.gm_natural_cli ingest <world_id> <game_day> <event_description>")
            return
        await ingest_event(
            world_id=sys.argv[2],
            game_day=int(sys.argv[3]),
            event_description=sys.argv[4],
        )

    elif command == "ingest-with-locations":
        if len(sys.argv) < 5:
            print("用法: python -m app.tools.gm_natural_cli ingest-with-locations <world_id> <game_day> <event_description>")
            return
        await ingest_with_locations(
            world_id=sys.argv[2],
            game_day=int(sys.argv[3]),
            event_description=sys.argv[4],
        )

    elif command == "show-gm":
        if len(sys.argv) < 3:
            print("用法: python -m app.tools.gm_natural_cli show-gm <world_id>")
            return
        await show_gm_graph(sys.argv[2])

    elif command == "show-char":
        if len(sys.argv) < 4:
            print("用法: python -m app.tools.gm_natural_cli show-char <world_id> <character_id>")
            return
        await show_character_graph(sys.argv[2], sys.argv[3])

    else:
        print(f"未知命令: {command}")
        print(__doc__)


if __name__ == "__main__":
    asyncio.run(main())
