#!/usr/bin/env python3
"""
Flash Natural Language CLI - 测试Flash的LLM增强功能

用法:
    # 首先设置角色profile
    python -m app.tools.flash_natural_cli setup <world_id> <character_id>

    # 事件摄入测试
    python -m app.tools.flash_natural_cli ingest <world_id> <character_id> "<事件描述>" <game_day>

    # 记忆检索测试
    python -m app.tools.flash_natural_cli recall <world_id> <character_id> "<查询>"

    # 查看角色图谱
    python -m app.tools.flash_natural_cli show <world_id> <character_id>

示例:
    python -m app.tools.flash_natural_cli setup test_world gorn
    python -m app.tools.flash_natural_cli ingest test_world gorn "一个冒险者来到我的铁匠铺，帮我修好了坏掉的炉子，还给了我一些金币作为感谢" 1
    python -m app.tools.flash_natural_cli recall test_world gorn "那个帮我修炉子的人"
"""
import asyncio
import json
import sys

from app.models.flash import NaturalEventIngestRequest, NaturalRecallRequest
from app.models.pro import CharacterProfile
from app.services.flash_service import FlashService
from app.services.graph_store import GraphStore


async def setup_character(world_id: str, character_id: str):
    """设置测试角色的profile"""
    graph_store = GraphStore()

    # 预设一些角色profile
    profiles = {
        "gorn": CharacterProfile(
            name="Gorn",
            occupation="铁匠",
            age=45,
            personality="性格粗犷但心地善良，对自己的手艺很自豪，不太善于表达感情",
            speech_pattern="说话简短有力，偶尔会用铁匠行话",
            example_dialogue="这把剑？花了我三天三夜。好钢，配得上好主人。",
        ),
        "marcus": CharacterProfile(
            name="Marcus",
            occupation="猎人",
            age=28,
            personality="机警谨慎，对森林了如指掌，有些孤僻但值得信赖",
            speech_pattern="说话轻声细语，喜欢用森林和动物的比喻",
            example_dialogue="风向变了...有东西在靠近，像狼群围猎一样。",
        ),
        "elena": CharacterProfile(
            name="Elena",
            occupation="酒馆老板娘",
            age=35,
            personality="热情开朗，八卦消息灵通，是镇上的信息中心",
            speech_pattern="说话快而热情，喜欢打听和分享故事",
            example_dialogue="哎呀，你还不知道吗？昨晚森林里可出大事了！来来来，坐下我慢慢告诉你...",
        ),
    }

    profile = profiles.get(character_id.lower())
    if not profile:
        profile = CharacterProfile(
            name=character_id,
            occupation="未知",
            personality="普通人",
            speech_pattern="正常说话",
        )

    await graph_store.set_character_profile(world_id, character_id, profile.model_dump())
    print(f"✓ 已设置角色 {character_id} 的profile:")
    print(f"  名字: {profile.name}")
    print(f"  职业: {profile.occupation}")
    print(f"  性格: {profile.personality}")


async def ingest_event(world_id: str, character_id: str, description: str, game_day: int):
    """测试事件摄入"""
    flash_service = FlashService()

    request = NaturalEventIngestRequest(
        event_description=description,
        game_day=game_day,
        write_indexes=True,
    )

    print(f"\n📝 事件摄入测试")
    print(f"世界: {world_id}, 角色: {character_id}")
    print(f"事件: {description}")
    print(f"游戏日: {game_day}")
    print("-" * 50)

    result = await flash_service.ingest_event_natural(world_id, character_id, request)

    print(f"\n✓ 摄入成功!")
    print(f"  事件ID: {result.event_id}")
    print(f"  创建节点: {result.node_count}")
    print(f"  创建边: {result.edge_count}")
    print(f"  状态更新: {result.state_updated}")

    if result.encoded_nodes:
        print(f"\n  编码的节点:")
        for node in result.encoded_nodes:
            print(f"    - {node.id} ({node.type}): {node.name}")
            if node.properties:
                for k, v in node.properties.items():
                    if k in ("summary", "emotion"):
                        print(f"      {k}: {v}")

    if result.encoded_edges:
        print(f"\n  编码的边:")
        for edge in result.encoded_edges:
            print(f"    - {edge.source} --{edge.relation}--> {edge.target}")


async def recall_memory(world_id: str, character_id: str, query: str):
    """测试记忆检索"""
    flash_service = FlashService()

    request = NaturalRecallRequest(
        query=query,
        translate=True,
        include_subgraph=True,
    )

    print(f"\n🔍 记忆检索测试")
    print(f"世界: {world_id}, 角色: {character_id}")
    print(f"查询: {query}")
    print("-" * 50)

    result = await flash_service.recall_memory_natural(world_id, character_id, request)

    print(f"\n检索意图: {result.search_intent}")
    print(f"种子节点: {result.seed_nodes}")

    if result.activated_nodes:
        print(f"\n激活的节点 (按激活值排序):")
        sorted_nodes = sorted(result.activated_nodes.items(), key=lambda x: x[1], reverse=True)
        for node_id, activation in sorted_nodes[:10]:
            print(f"  [{activation:.2f}] {node_id}")

    if result.translated_memory:
        print(f"\n💭 角色回忆:")
        print("-" * 30)
        print(result.translated_memory)
        print("-" * 30)

    if result.note:
        print(f"\n⚠️ 备注: {result.note}")


async def show_graph(world_id: str, character_id: str):
    """显示角色的记忆图谱"""
    graph_store = GraphStore()

    # 获取profile
    profile_data = await graph_store.get_character_profile(world_id, character_id)
    if profile_data:
        print(f"\n👤 角色Profile:")
        print(f"  名字: {profile_data.get('name', '未知')}")
        print(f"  职业: {profile_data.get('occupation', '未知')}")
        print(f"  性格: {profile_data.get('personality', '未知')}")

    # 获取状态
    state = await graph_store.get_character_state(world_id, character_id)
    if state:
        print(f"\n📊 角色状态:")
        print(f"  {json.dumps(state, ensure_ascii=False, indent=2)}")

    # 获取图谱
    graph_data = await graph_store.load_graph(world_id, "character", character_id)

    if not graph_data or not graph_data.nodes:
        print(f"\n⚠️ 角色 {character_id} 在世界 {world_id} 中没有记忆图谱")
        return

    nodes = graph_data.nodes
    edges = graph_data.edges

    print(f"\n📊 记忆图谱:")
    print(f"  节点数: {len(nodes)}")
    print(f"  边数: {len(edges)}")

    if nodes:
        print(f"\n  节点列表:")
        # 按类型分组
        by_type = {}
        for node in nodes:
            t = node.type
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(node)

        for node_type, type_nodes in by_type.items():
            print(f"\n  [{node_type}] ({len(type_nodes)}个)")
            for node in type_nodes[:5]:
                importance = node.importance
                print(f"    - {node.id}: {node.name} (重要度: {importance:.2f})")
                props = node.properties or {}
                if "summary" in props:
                    print(f"      摘要: {props['summary'][:50]}...")
            if len(type_nodes) > 5:
                print(f"    ... 还有 {len(type_nodes) - 5} 个")

    if edges:
        print(f"\n  边列表 (前10条):")
        for edge in edges[:10]:
            print(f"    {edge.source} --{edge.relation}--> {edge.target}")
        if len(edges) > 10:
            print(f"    ... 还有 {len(edges) - 10} 条")


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1]

    if command == "setup":
        if len(sys.argv) < 4:
            print("用法: python -m app.tools.flash_natural_cli setup <world_id> <character_id>")
            return
        await setup_character(sys.argv[2], sys.argv[3])

    elif command == "ingest":
        if len(sys.argv) < 6:
            print("用法: python -m app.tools.flash_natural_cli ingest <world_id> <character_id> <description> <game_day>")
            return
        await ingest_event(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]))

    elif command == "recall":
        if len(sys.argv) < 5:
            print("用法: python -m app.tools.flash_natural_cli recall <world_id> <character_id> <query>")
            return
        await recall_memory(sys.argv[2], sys.argv[3], sys.argv[4])

    elif command == "show":
        if len(sys.argv) < 4:
            print("用法: python -m app.tools.flash_natural_cli show <world_id> <character_id>")
            return
        await show_graph(sys.argv[2], sys.argv[3])

    else:
        print(f"未知命令: {command}")
        print(__doc__)


if __name__ == "__main__":
    asyncio.run(main())
