"""
世界初始化 CLI 统一入口

用法：
    # 阶段 1: 世界书图谱化
    python -m app.tools.init_world_cli graphize \
        --worldbook data/goblin_slayer/worldbook_full.md \
        --output data/goblin_slayer/structured/

    # 阶段 2: 初始化到 Firestore
    python -m app.tools.init_world_cli load \
        --world goblin_slayer \
        --data-dir data/goblin_slayer/structured/

    # 仅加载地图
    python -m app.tools.init_world_cli load --world goblin_slayer --maps-only

    # 仅加载特定角色
    python -m app.tools.init_world_cli load --world goblin_slayer --character priestess

    # 验证模式
    python -m app.tools.init_world_cli load --world goblin_slayer --dry-run

    # 验证世界状态
    python -m app.tools.init_world_cli verify --world goblin_slayer
"""
import argparse
import asyncio
import sys
from pathlib import Path


async def cmd_graphize(args: argparse.Namespace) -> int:
    """执行世界书图谱化"""
    from app.tools.worldbook_graphizer import WorldbookGraphizer

    worldbook_path = Path(args.worldbook)
    output_dir = Path(args.output)

    if not worldbook_path.exists():
        print(f"Error: Worldbook file not found: {worldbook_path}")
        return 1

    print(f"Graphizing worldbook: {worldbook_path}")
    print(f"Output directory: {output_dir}")
    print(f"Model: {args.model}")
    print()

    try:
        graphizer = WorldbookGraphizer(model=args.model)
        result = await graphizer.graphize(
            worldbook_path=worldbook_path,
            output_dir=output_dir,
            validate=not args.no_validate,
            verbose=not args.quiet,
        )

        print(f"\nOutput files:")
        print(f"  - {output_dir / 'maps.json'}")
        print(f"  - {output_dir / 'characters.json'}")
        print(f"  - {output_dir / 'world_map.json'}")
        print(f"  - {output_dir / 'character_profiles.json'}")
        print(f"  - {output_dir / 'metadata.json'}")

        return 0

    except Exception as e:
        print(f"\nError during graphization: {str(e)}")
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1


async def cmd_load(args: argparse.Namespace) -> int:
    """加载世界数据到 Firestore"""
    from app.tools.world_initializer import WorldInitializer

    world_id = args.world
    data_dir = Path(args.data_dir) if args.data_dir else None

    print(f"Loading world: {world_id}")
    if args.dry_run:
        print("[DRY RUN MODE - No changes will be made]")
    print()

    try:
        initializer = WorldInitializer()

        if args.maps_only:
            # 仅加载地图
            if not data_dir:
                print("Error: --data-dir required for --maps-only")
                return 1
            maps_path = data_dir / "maps.json"
            result = await initializer.initialize_maps_only(
                world_id=world_id,
                maps_path=maps_path,
                dry_run=args.dry_run,
                verbose=not args.quiet,
            )
            print(f"\nMaps loaded: {result.get('maps_loaded', 0)}")

        elif args.character:
            # 加载单个角色
            if not data_dir:
                print("Error: --data-dir required for --character")
                return 1
            chars_path = data_dir / "characters.json"
            success = await initializer.initialize_character(
                world_id=world_id,
                character_id=args.character,
                chars_path=chars_path,
                dry_run=args.dry_run,
                verbose=not args.quiet,
            )
            if success:
                print(f"\nCharacter '{args.character}' loaded successfully")
            else:
                print(f"\nFailed to load character '{args.character}'")
                return 1

        else:
            # 完整初始化
            if not data_dir:
                print("Error: --data-dir required")
                return 1
            result = await initializer.initialize(
                world_id=world_id,
                data_dir=data_dir,
                dry_run=args.dry_run,
                verbose=not args.quiet,
            )

            if result.get("errors"):
                print(f"\nCompleted with {len(result['errors'])} errors")
                return 1

        return 0

    except Exception as e:
        print(f"\nError during initialization: {str(e)}")
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1


async def cmd_verify(args: argparse.Namespace) -> int:
    """验证世界初始化状态"""
    from app.tools.world_initializer import WorldInitializer

    world_id = args.world

    try:
        initializer = WorldInitializer()
        result = await initializer.verify_initialization(
            world_id=world_id,
            verbose=not args.quiet,
        )

        # 检查是否有数据
        has_data = bool(result.get("maps") or result.get("characters"))
        if not has_data:
            print(f"\nWarning: World '{world_id}' appears to be empty")
            return 1

        return 0

    except Exception as e:
        print(f"\nError during verification: {str(e)}")
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1


async def cmd_graphize_maps(args: argparse.Namespace) -> int:
    """仅提取地图数据"""
    from app.tools.worldbook_graphizer import WorldbookGraphizer

    worldbook_path = Path(args.worldbook)
    output_path = Path(args.output)

    if not worldbook_path.exists():
        print(f"Error: Worldbook file not found: {worldbook_path}")
        return 1

    print(f"Extracting maps from: {worldbook_path}")
    print(f"Output: {output_path}")
    print()

    try:
        graphizer = WorldbookGraphizer(model=args.model)
        maps_data = await graphizer.graphize_maps_only(
            worldbook_path=worldbook_path,
            output_path=output_path,
        )

        print(f"\nExtracted {len(maps_data.maps)} maps")
        for m in maps_data.maps[:10]:
            print(f"  - {m.id}: {m.name}")
        if len(maps_data.maps) > 10:
            print(f"  ... and {len(maps_data.maps) - 10} more")

        return 0

    except Exception as e:
        print(f"\nError: {str(e)}")
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1


async def cmd_graphize_characters(args: argparse.Namespace) -> int:
    """仅提取角色数据"""
    from app.tools.worldbook_graphizer import WorldbookGraphizer

    worldbook_path = Path(args.worldbook)
    output_path = Path(args.output)
    maps_path = Path(args.maps) if args.maps else None

    if not worldbook_path.exists():
        print(f"Error: Worldbook file not found: {worldbook_path}")
        return 1

    print(f"Extracting characters from: {worldbook_path}")
    if maps_path:
        print(f"Using maps from: {maps_path}")
    print(f"Output: {output_path}")
    print()

    try:
        graphizer = WorldbookGraphizer(model=args.model)
        chars_data = await graphizer.graphize_characters_only(
            worldbook_path=worldbook_path,
            maps_path=maps_path,
            output_path=output_path,
        )

        print(f"\nExtracted {len(chars_data.characters)} characters")
        for c in chars_data.characters[:10]:
            print(f"  - {c.id}: {c.name} ({c.tier.value})")
        if len(chars_data.characters) > 10:
            print(f"  ... and {len(chars_data.characters) - 10} more")

        return 0

    except Exception as e:
        print(f"\nError: {str(e)}")
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1


async def cmd_graphize_tavern(args: argparse.Namespace) -> int:
    """从酒馆卡片提取知识图谱 (使用 Batch API)"""
    from app.tools.worldbook_graphizer.graph_extractor import GraphExtractor

    input_path = Path(args.input)
    output_path = Path(args.output)
    batch_dir = Path(args.batch_dir) if args.batch_dir else None

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        return 1

    print(f"Extracting knowledge graph from tavern card: {input_path}")
    print(f"Output: {output_path}")
    print(f"Model: {args.model}")
    if batch_dir:
        print(f"Batch temp dir: {batch_dir}")
    print()

    try:
        extractor = GraphExtractor(
            model=args.model,
            verbose=not args.quiet,
        )

        # 构建图谱 (使用 Batch API)
        graph_data = await extractor.build_graph(input_path, output_dir=batch_dir)

        # 保存结果
        extractor.save_graph(graph_data, output_path)

        # 打印节点类型分布
        type_counts = {}
        for node in graph_data.nodes:
            type_counts[node.type] = type_counts.get(node.type, 0) + 1

        print("\nNode type distribution:")
        for node_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"  {node_type}: {count}")

        # 打印关系类型分布
        rel_counts = {}
        for edge in graph_data.edges:
            rel_counts[edge.relation] = rel_counts.get(edge.relation, 0) + 1

        print("\nRelation type distribution:")
        for rel_type, count in sorted(rel_counts.items(), key=lambda x: -x[1]):
            print(f"  {rel_type}: {count}")

        return 0

    except Exception as e:
        print(f"\nError: {str(e)}")
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1


def main() -> int:
    """主入口"""
    parser = argparse.ArgumentParser(
        description="世界初始化 CLI - 图谱化世界书并加载到 Firestore",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 完整图谱化
  python -m app.tools.init_world_cli graphize \\
      --worldbook data/goblin_slayer/worldbook_full.md \\
      --output data/goblin_slayer/structured/

  # 加载到 Firestore
  python -m app.tools.init_world_cli load \\
      --world goblin_slayer \\
      --data-dir data/goblin_slayer/structured/

  # 验证模式（不实际写入）
  python -m app.tools.init_world_cli load \\
      --world goblin_slayer \\
      --data-dir data/goblin_slayer/structured/ \\
      --dry-run

  # 验证世界状态
  python -m app.tools.init_world_cli verify --world goblin_slayer
        """
    )

    # 全局参数
    parser.add_argument("--debug", action="store_true", help="显示详细错误信息")
    parser.add_argument("--quiet", "-q", action="store_true", help="减少输出")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ============ graphize 命令 ============
    graphize_parser = subparsers.add_parser(
        "graphize",
        help="从世界书提取结构化数据"
    )
    graphize_parser.add_argument(
        "--worldbook", "-w",
        required=True,
        help="世界书文件路径 (markdown)"
    )
    graphize_parser.add_argument(
        "--output", "-o",
        required=True,
        help="输出目录"
    )
    graphize_parser.add_argument(
        "--model", "-m",
        default="gemini-2.0-flash",
        help="Gemini 模型 (默认: gemini-2.0-flash)"
    )
    graphize_parser.add_argument(
        "--no-validate",
        action="store_true",
        help="跳过验证"
    )

    # ============ graphize-maps 命令 ============
    graphize_maps_parser = subparsers.add_parser(
        "graphize-maps",
        help="仅提取地图数据"
    )
    graphize_maps_parser.add_argument(
        "--worldbook", "-w",
        required=True,
        help="世界书文件路径"
    )
    graphize_maps_parser.add_argument(
        "--output", "-o",
        required=True,
        help="输出文件路径 (maps.json)"
    )
    graphize_maps_parser.add_argument(
        "--model", "-m",
        default="gemini-2.0-flash",
        help="Gemini 模型"
    )

    # ============ graphize-characters 命令 ============
    graphize_chars_parser = subparsers.add_parser(
        "graphize-characters",
        help="仅提取角色数据"
    )
    graphize_chars_parser.add_argument(
        "--worldbook", "-w",
        required=True,
        help="世界书文件路径"
    )
    graphize_chars_parser.add_argument(
        "--output", "-o",
        required=True,
        help="输出文件路径 (characters.json)"
    )
    graphize_chars_parser.add_argument(
        "--maps",
        help="已提取的地图文件路径（可选，用于关联 NPC 到地图）"
    )
    graphize_chars_parser.add_argument(
        "--model", "-m",
        default="gemini-2.0-flash",
        help="Gemini 模型"
    )

    # ============ graphize-tavern 命令 ============
    graphize_tavern_parser = subparsers.add_parser(
        "graphize-tavern",
        help="从酒馆卡片 JSON 提取知识图谱 (使用 Batch API)"
    )
    graphize_tavern_parser.add_argument(
        "--input", "-i",
        required=True,
        help="酒馆卡片 JSON 文件路径"
    )
    graphize_tavern_parser.add_argument(
        "--output", "-o",
        required=True,
        help="输出文件路径 (world_graph.json)"
    )
    graphize_tavern_parser.add_argument(
        "--model", "-m",
        default="gemini-3-flash-preview",
        help="Gemini 模型 (默认: gemini-3-flash-preview)"
    )
    graphize_tavern_parser.add_argument(
        "--batch-dir",
        default=None,
        help="批量处理中间文件目录 (默认: 输出目录同级的 batch_temp)"
    )

    # ============ load 命令 ============
    load_parser = subparsers.add_parser(
        "load",
        help="加载结构化数据到 Firestore"
    )
    load_parser.add_argument(
        "--world",
        required=True,
        help="世界 ID"
    )
    load_parser.add_argument(
        "--data-dir",
        help="结构化数据目录"
    )
    load_parser.add_argument(
        "--maps-only",
        action="store_true",
        help="仅加载地图"
    )
    load_parser.add_argument(
        "--character",
        help="仅加载指定角色"
    )
    load_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="模拟执行（不实际写入）"
    )

    # ============ verify 命令 ============
    verify_parser = subparsers.add_parser(
        "verify",
        help="验证世界初始化状态"
    )
    verify_parser.add_argument(
        "--world",
        required=True,
        help="世界 ID"
    )

    args = parser.parse_args()

    # 路由到对应命令
    if args.command == "graphize":
        return asyncio.run(cmd_graphize(args))
    elif args.command == "graphize-maps":
        return asyncio.run(cmd_graphize_maps(args))
    elif args.command == "graphize-characters":
        return asyncio.run(cmd_graphize_characters(args))
    elif args.command == "graphize-tavern":
        return asyncio.run(cmd_graphize_tavern(args))
    elif args.command == "load":
        return asyncio.run(cmd_load(args))
    elif args.command == "verify":
        return asyncio.run(cmd_verify(args))
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
