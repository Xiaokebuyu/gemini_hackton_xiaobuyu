"""
世界初始化 CLI 统一入口

用法：
    # 统一提取管线（推荐）—— 从酒馆卡片一步生成全部结构化文件
    # 默认 Batch API 模式（50% 成本优惠，需等待排队）
    python -m app.tools.init_world_cli extract \
        --input "worldbook.json" \
        --output data/goblin_slayer/structured/ \
        --model gemini-3-pro-preview \
        --relabel-edges --enrich-entities

    # 若 Batch API 不支持 thinking_config，加 --thinking-level none
    python -m app.tools.init_world_cli extract \
        --input "worldbook.json" \
        --output data/goblin_slayer/structured/ \
        --model gemini-3-pro-preview \
        --thinking-level none \
        --relabel-edges --enrich-entities

    # 直接调用模式（实时返回，无成本优惠）
    python -m app.tools.init_world_cli extract \
        --input "worldbook.json" \
        --output data/goblin_slayer/structured/ \
        --model gemini-3-pro-preview \
        --direct --relabel-edges --enrich-entities

    # 阶段 1: 世界书图谱化（旧接口）
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
import json
import re
import sys
from pathlib import Path


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


async def cmd_extract(args: argparse.Namespace) -> int:
    """统一世界书提取管线：从酒馆卡片 JSON 一步生成全部结构化文件"""
    from app.tools.worldbook_graphizer.unified_pipeline import UnifiedWorldExtractor

    input_path = Path(args.input)
    output_dir = Path(args.output)
    mainlines_path = Path(args.mainlines) if args.mainlines else None

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        return 1

    if mainlines_path and not mainlines_path.exists():
        print(f"Error: Mainlines file not found: {mainlines_path}")
        return 1

    thinking_level = args.thinking_level if args.thinking_level != "none" else None
    use_direct = args.direct

    print(f"Unified extraction pipeline")
    print(f"  Input:    {input_path}")
    print(f"  Output:   {output_dir}")
    print(f"  Model:    {args.model}")
    print(f"  Thinking: {thinking_level}")
    print(f"  Mode:     {'direct' if use_direct else 'batch'}")
    if mainlines_path:
        print(f"  Mainlines: {mainlines_path}")
    print()

    try:
        extractor = UnifiedWorldExtractor(
            model=args.model,
            verbose=not args.quiet,
            thinking_level=thinking_level,
        )

        stats = await extractor.extract(
            lorebook_path=input_path,
            output_dir=output_dir,
            mainlines_path=mainlines_path,
            validate=not args.no_validate,
            use_direct=use_direct,
            relabel_edges=args.relabel_edges,
            enrich_entities=args.enrich_entities,
        )

        # 打印输出文件状态
        print("\nOutput files:")
        expected_files = [
            "maps.json", "characters.json", "world_map.json",
            "character_profiles.json", "world_graph.json",
            "prefilled_graph.json", "chapters_v2.json",
            "monsters.json", "items.json", "skills.json",
        ]
        for fname in expected_files:
            fpath = output_dir / fname
            if fpath.exists():
                size = fpath.stat().st_size
                print(f"  ✓ {fname:30s} ({size:,} bytes)")
            else:
                print(f"  ✗ {fname:30s} (missing)")

        return 0

    except Exception as e:
        print(f"\nError during extraction: {str(e)}")
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


async def cmd_enrich_mainlines(args: argparse.Namespace) -> int:
    """增强 mainlines.json 中的章节数据（分类 + 结构化提取）"""
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        return 1

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    chapters = data.get("chapters", [])
    print(f"Loaded {len(chapters)} chapters")

    # Step 1: 正则分类 + 基础提取
    metadata_keywords = ["状态栏", "剧情系统", "剧情初始化", "章节管理器"]
    volume_pattern = re.compile(r"^[📖📚\s]*第[一二三四五六七八九十\d]+卷[^章]*$")
    objective_pattern = re.compile(r"(?:主要目标|章节目标)[：:]\s*(.+?)(?:\n|$)")
    event_list_pattern = re.compile(r"<第\d+章事件列表>(.*?)</", re.DOTALL)
    event_line_pattern = re.compile(r"章节事件[：:]\s*(.+?)(?:\n|$)")

    story_chapters = []
    stats = {"metadata": 0, "volume_index": 0, "story": 0}

    for ch in chapters:
        name = ch.get("name", "")
        ch_id = ch.get("id", "")
        description = ch.get("description", "")

        # 分类
        if any(kw in name for kw in metadata_keywords):
            ch["type"] = "metadata"
            stats["metadata"] += 1
            continue

        name_stripped = re.sub(r"[📖📚\s]", "", name)
        if volume_pattern.match(name) or (
            re.match(r"第[一二三四五六七八九十\d]+卷", name_stripped) and "章" not in name_stripped
        ):
            ch["type"] = "volume_index"
            stats["volume_index"] += 1
            continue

        ch["type"] = "story"
        stats["story"] += 1

        # 提取目标
        if not ch.get("objectives"):
            objectives = []
            for match in objective_pattern.finditer(description):
                obj_text = match.group(1).strip()
                if obj_text:
                    objectives.append(obj_text)
            if objectives:
                ch["objectives"] = objectives

        # 提取事件
        existing_events = (ch.get("completion_conditions") or {}).get("events_required", [])
        if not existing_events:
            events = []
            for match in event_list_pattern.finditer(description):
                event_block = match.group(1)
                for line in event_block.strip().splitlines():
                    line = line.strip(" -*·")
                    if line:
                        events.append(f"{ch_id}_{line.replace(' ', '_')[:30]}")

            for match in event_line_pattern.finditer(description):
                event_text = match.group(1).strip()
                if event_text:
                    for ev in re.split(r"[,，、;；]", event_text):
                        ev = ev.strip()
                        if ev:
                            events.append(f"{ch_id}_{ev.replace(' ', '_')[:30]}")

            if events:
                if "completion_conditions" not in ch:
                    ch["completion_conditions"] = {}
                ch["completion_conditions"]["events_required"] = events

        # 需要 LLM 增强的章节
        if not ch.get("available_maps") or not ch.get("objectives"):
            story_chapters.append(ch)

    print(f"\nClassification: {stats}")
    print(f"Chapters needing LLM enrichment: {len(story_chapters)}")

    # Step 2: LLM 批量增强
    if story_chapters and not args.regex_only:
        from app.services.llm_service import LLMService

        llm = LLMService()
        prompt_path = Path("app/tools/worldbook_graphizer/prompts/mainline_enrichment.md")
        if not prompt_path.exists():
            print(f"Warning: Prompt template not found: {prompt_path}")
            print("Skipping LLM enrichment")
        else:
            prompt_template = prompt_path.read_text(encoding="utf-8")
            known_maps = (
                args.known_maps
                or "frontier_town, cow_girl_farm, water_capital, training_grounds, goblin_cave, ruins_of_the_dead, elf_forest"
            )

            enriched_count = 0
            for i, ch in enumerate(story_chapters):
                ch_id = ch.get("id", "unknown")
                print(f"  [{i+1}/{len(story_chapters)}] Enriching {ch_id}...", end=" ", flush=True)

                existing_objectives = ch.get("objectives", [])
                if isinstance(existing_objectives, list) and existing_objectives:
                    obj_text = "\n".join(
                        f"- {o}" if isinstance(o, str) else f"- {o.get('description', '')}"
                        for o in existing_objectives
                    )
                else:
                    obj_text = "无"

                prompt = prompt_template.format(
                    chapter_id=ch_id,
                    chapter_name=ch.get("name", ""),
                    chapter_description=ch.get("description", "")[:2000],
                    known_maps=known_maps,
                    existing_objectives=obj_text,
                )

                try:
                    result = await llm.generate_simple(prompt, model_override=args.model)
                    parsed = llm.parse_json(result)
                    if parsed:
                        if parsed.get("available_maps") and not ch.get("available_maps"):
                            ch["available_maps"] = parsed["available_maps"]
                        if parsed.get("objectives") and not ch.get("objectives"):
                            ch["objectives"] = parsed["objectives"]
                        if parsed.get("completion_conditions"):
                            if "completion_conditions" not in ch:
                                ch["completion_conditions"] = {}
                            cc = parsed["completion_conditions"]
                            if cc.get("events_required") and not ch["completion_conditions"].get("events_required"):
                                ch["completion_conditions"]["events_required"] = cc["events_required"]
                        enriched_count += 1
                        print("OK")
                    else:
                        print("PARSE_FAILED")
                except Exception as exc:
                    print(f"ERROR: {exc}")

            print(f"\nLLM enriched: {enriched_count}/{len(story_chapters)}")

    # 保存
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\nSaved to: {output_path}")

    # 统计
    total_story = sum(1 for ch in data["chapters"] if ch.get("type") == "story")
    story_with_objectives = sum(
        1 for ch in data["chapters"] if ch.get("type") == "story" and ch.get("objectives")
    )
    story_with_maps = sum(
        1 for ch in data["chapters"] if ch.get("type") == "story" and ch.get("available_maps")
    )
    print(f"Story chapters with objectives: {story_with_objectives}/{total_story}")
    print(f"Story chapters with available_maps: {story_with_maps}/{total_story}")

    return 0


async def cmd_relabel_edges(args: argparse.Namespace) -> int:
    """重标注 world_graph.json 中的边类型"""
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        return 1

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    edges = data.get("edges", [])
    nodes = data.get("nodes", [])

    # 构建节点查找表
    node_lookup = {}
    for node in nodes:
        node_id = node.get("id", "")
        node_lookup[node_id] = {
            "name": node.get("name", node_id),
            "type": node.get("type", "unknown"),
        }

    # 找出需要重标注的边
    unknown_edges = [
        e for e in edges if e.get("relation", "unknown") in ("unknown", "related", "")
    ]
    print(f"Total edges: {len(edges)}")
    print(f"Edges needing relabeling: {len(unknown_edges)}")

    if not unknown_edges:
        print("No edges need relabeling.")
        return 0

    from app.services.llm_service import LLMService

    llm = LLMService()
    prompt_path = Path("app/tools/worldbook_graphizer/prompts/edge_relabeling.md")
    if not prompt_path.exists():
        print(f"Error: Prompt template not found: {prompt_path}")
        return 1

    prompt_template = prompt_path.read_text(encoding="utf-8")

    # 分批处理
    batch_size = int(args.batch_size)
    relabeled_count = 0
    edge_id_to_relation = {}

    for batch_start in range(0, len(unknown_edges), batch_size):
        batch = unknown_edges[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        total_batches = (len(unknown_edges) + batch_size - 1) // batch_size
        print(f"  Batch {batch_num}/{total_batches} ({len(batch)} edges)...", end=" ", flush=True)

        # 格式化批次
        edge_lines = []
        for edge in batch:
            edge_id = edge.get("id", "")
            source_id = edge.get("source", "")
            target_id = edge.get("target", "")
            source_info = node_lookup.get(source_id, {"name": source_id, "type": "?"})
            target_info = node_lookup.get(target_id, {"name": target_id, "type": "?"})
            edge_lines.append(
                f"- edge_id: {edge_id} | "
                f"source: {source_info['name']} (type={source_info['type']}) | "
                f"target: {target_info['name']} (type={target_info['type']})"
            )

        edges_batch = "\n".join(edge_lines)
        prompt = prompt_template.format(edges_batch=edges_batch)

        try:
            result = await llm.generate_simple(prompt, model_override=args.model)
            parsed = llm.parse_json(result)
            if isinstance(parsed, list):
                for item in parsed:
                    eid = item.get("edge_id", "")
                    rel = item.get("relation", "related_to")
                    if eid:
                        edge_id_to_relation[eid] = rel
                        relabeled_count += 1
                print(f"OK ({len(parsed)} labeled)")
            elif isinstance(parsed, dict) and "edges" in parsed:
                for item in parsed["edges"]:
                    eid = item.get("edge_id", "")
                    rel = item.get("relation", "related_to")
                    if eid:
                        edge_id_to_relation[eid] = rel
                        relabeled_count += 1
                print(f"OK ({len(parsed['edges'])} labeled)")
            else:
                print("PARSE_FAILED")
        except Exception as exc:
            print(f"ERROR: {exc}")

    # 应用标注
    for edge in edges:
        edge_id = edge.get("id", "")
        if edge_id in edge_id_to_relation:
            edge["relation"] = edge_id_to_relation[edge_id]

    # 保存
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 同步更新 prefilled_graph.json
    prefilled_path = output_path.parent / "prefilled_graph.json"
    if prefilled_path.exists():
        with open(prefilled_path, "r", encoding="utf-8") as f:
            prefilled = json.load(f)
        for edge in prefilled.get("edges", []):
            edge_id = edge.get("id", "")
            if edge_id in edge_id_to_relation:
                edge["relation"] = edge_id_to_relation[edge_id]
        with open(prefilled_path, "w", encoding="utf-8") as f:
            json.dump(prefilled, f, ensure_ascii=False, indent=2)
        print(f"Also updated: {prefilled_path}")

    # 统计
    remaining_unknown = sum(
        1 for e in edges if e.get("relation", "unknown") in ("unknown", "related", "")
    )
    print(f"\nRelabeled: {relabeled_count}/{len(unknown_edges)}")
    print(f"Remaining unknown: {remaining_unknown}/{len(edges)} ({remaining_unknown/len(edges)*100:.1f}%)")
    print(f"Saved to: {output_path}")

    return 0


async def cmd_enrich_entities(args: argparse.Namespace) -> int:
    """从原始世界书提取 D&D 实体数据（怪物、物品、技能）"""
    input_path = Path(args.input)
    output_dir = Path(args.output)

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    # 加载酒馆卡片
    with open(input_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    # 提取条目（兼容多种 lorebook 格式）
    entries = []
    if "entries" in raw_data:
        entries = list(raw_data["entries"].values()) if isinstance(raw_data["entries"], dict) else raw_data["entries"]
    elif "data" in raw_data and isinstance(raw_data["data"], dict):
        raw_entries = raw_data["data"].get("entries", {})
        entries = list(raw_entries.values()) if isinstance(raw_entries, dict) else raw_entries

    if not entries:
        print("Warning: No entries found in input file")
        return 1

    print(f"Loaded {len(entries)} entries")

    # 加载 world_graph 用于交叉引用
    world_graph_path = output_dir / "world_graph.json"
    existing_nodes = {}
    if world_graph_path.exists():
        with open(world_graph_path, "r", encoding="utf-8") as f:
            wg = json.load(f)
        for node in wg.get("nodes", []):
            existing_nodes[node.get("id", "")] = node

    # 分类条目
    monsters_raw, items_raw, skills_raw = [], [], []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_keys = entry.get("key", entry.get("keys", []))
        if isinstance(entry_keys, str):
            entry_keys = [entry_keys]
        entry_name = entry.get("comment", "") or entry.get("name", "")
        extensions = entry.get("extensions", {}) or {}
        entry_tags = extensions.get("tag", []) if isinstance(extensions, dict) else []

        all_text = f"{entry_name} {' '.join(entry_keys)} {' '.join(entry_tags)}".lower()

        if any(kw in all_text for kw in ["monster", "怪物", "魔物", "boss", "敌人"]):
            monsters_raw.append(entry)
        elif any(kw in all_text for kw in ["item", "物品", "武器", "防具", "道具", "装备"]):
            items_raw.append(entry)
        elif any(kw in all_text for kw in ["skill", "技能", "法术", "魔法", "奇迹", "能力"]):
            skills_raw.append(entry)

    print(f"Categorized: {len(monsters_raw)} monsters, {len(items_raw)} items, {len(skills_raw)} skills")

    from app.services.llm_service import LLMService
    llm = LLMService()

    async def _process_category(category_entries, prompt_file, category_name):
        prompt_path = Path(f"app/tools/worldbook_graphizer/prompts/{prompt_file}")
        if not prompt_path.exists():
            print(f"Warning: {prompt_path} not found, skipping {category_name}")
            return []

        prompt_template = prompt_path.read_text(encoding="utf-8")
        results = []

        for i, entry in enumerate(category_entries):
            entry_name = entry.get("comment", "") or entry.get("name", "")
            entry_id = entry_name.replace(" ", "_").lower() or f"{category_name}_{i}"
            entry_desc = entry.get("content", "") or entry.get("description", "")

            existing = existing_nodes.get(entry_id, {})
            existing_props = existing.get("properties", {}) if existing else {}
            existing_text = json.dumps(existing_props, ensure_ascii=False) if existing_props else "无"

            print(f"  [{i+1}/{len(category_entries)}] {entry_name}...", end=" ", flush=True)

            prompt = prompt_template.format(
                **{
                    f"{category_name}_id": entry_id,
                    f"{category_name}_name": entry_name,
                    f"{category_name}_description": entry_desc[:3000],
                    "existing_properties": existing_text,
                }
            )

            try:
                result = await llm.generate_simple(prompt, model_override=args.model)
                parsed = llm.parse_json(result)
                if parsed:
                    results.append(parsed)
                    print("OK")
                else:
                    print("PARSE_FAILED")
            except Exception as exc:
                print(f"ERROR: {exc}")

        return results

    # 处理各类实体
    if monsters_raw:
        print(f"\nProcessing {len(monsters_raw)} monsters...")
        monsters = await _process_category(monsters_raw, "monster_extraction.md", "monster")
        with open(output_dir / "monsters.json", "w", encoding="utf-8") as f:
            json.dump({"monsters": monsters}, f, ensure_ascii=False, indent=2)
        print(f"Saved {len(monsters)} monsters to monsters.json")

    if items_raw:
        print(f"\nProcessing {len(items_raw)} items...")
        items = await _process_category(items_raw, "item_extraction.md", "item")
        with open(output_dir / "items.json", "w", encoding="utf-8") as f:
            json.dump({"items": items}, f, ensure_ascii=False, indent=2)
        print(f"Saved {len(items)} items to items.json")

    if skills_raw:
        print(f"\nProcessing {len(skills_raw)} skills...")
        skills = await _process_category(skills_raw, "skill_extraction.md", "skill")
        with open(output_dir / "skills.json", "w", encoding="utf-8") as f:
            json.dump({"skills": skills}, f, ensure_ascii=False, indent=2)
        print(f"Saved {len(skills)} skills to skills.json")

    return 0


def main() -> int:
    """主入口"""
    parser = argparse.ArgumentParser(
        description="世界初始化 CLI - 图谱化世界书并加载到 Firestore",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 从酒馆卡片 JSON 提取结构化数据（推荐）
  python -m app.tools.init_world_cli extract \\
      --input data/goblin_slayer/worldbook.json \\
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

    # ============ graphize-tavern 命令 ============
    graphize_tavern_parser = subparsers.add_parser(
        "graphize-tavern",
        help="[已弃用] 请使用 extract 命令。从酒馆卡片 JSON 提取知识图谱 (使用 Batch API)"
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

    # ============ extract 命令（统一管线） ============
    extract_parser = subparsers.add_parser(
        "extract",
        help="统一提取管线：从酒馆卡片 JSON 生成全部结构化文件"
    )
    extract_parser.add_argument(
        "--input", "-i",
        required=True,
        help="SillyTavern V2 Lorebook JSON 文件路径"
    )
    extract_parser.add_argument(
        "--output", "-o",
        required=True,
        help="输出目录"
    )
    extract_parser.add_argument(
        "--model", "-m",
        default="gemini-3-pro-preview",
        help="Gemini 模型 (默认: gemini-3-pro-preview)"
    )
    extract_parser.add_argument(
        "--mainlines",
        default=None,
        help="mainlines.json 路径（可选，无则自动生成默认章节）"
    )
    extract_parser.add_argument(
        "--thinking-level",
        default="high",
        choices=["none", "lowest", "low", "medium", "high"],
        help="思考级别 (默认: high, none=禁用, Batch API 不支持 thinking 时用 none)"
    )
    extract_parser.add_argument(
        "--direct",
        action="store_true",
        help="使用直接 LLM 调用而非 Batch API（更快，但无成本优惠）"
    )
    extract_parser.add_argument(
        "--no-validate",
        action="store_true",
        help="跳过验证"
    )
    extract_parser.add_argument(
        "--relabel-edges",
        action="store_true",
        help="重标注 unknown 边类型（LLM 增强）"
    )
    extract_parser.add_argument(
        "--enrich-entities",
        action="store_true",
        help="提取 D&D 实体数据（怪物/物品/技能）"
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

    # ============ enrich-mainlines 命令 ============
    enrich_mainlines_parser = subparsers.add_parser(
        "enrich-mainlines",
        help="增强 mainlines.json 章节数据（分类 + 结构化提取）"
    )
    enrich_mainlines_parser.add_argument(
        "--input", "-i", required=True, help="mainlines.json 路径"
    )
    enrich_mainlines_parser.add_argument(
        "--output", "-o", required=True, help="输出路径"
    )
    enrich_mainlines_parser.add_argument(
        "--model", "-m", default="gemini-3-flash-preview", help="Gemini 模型"
    )
    enrich_mainlines_parser.add_argument(
        "--regex-only", action="store_true", help="仅正则提取，跳过 LLM 增强"
    )
    enrich_mainlines_parser.add_argument(
        "--known-maps", default=None, help="已知地图列表（逗号分隔）"
    )

    # ============ relabel-edges 命令 ============
    relabel_edges_parser = subparsers.add_parser(
        "relabel-edges",
        help="重标注 world_graph.json 边类型（unknown → CRPGRelationType）"
    )
    relabel_edges_parser.add_argument(
        "--input", "-i", required=True, help="world_graph.json 路径"
    )
    relabel_edges_parser.add_argument(
        "--output", "-o", required=True, help="输出路径"
    )
    relabel_edges_parser.add_argument(
        "--model", "-m", default="gemini-3-flash-preview", help="Gemini 模型"
    )
    relabel_edges_parser.add_argument(
        "--batch-size", default="30", help="每批处理的边数（默认: 30）"
    )

    # ============ enrich-entities 命令 ============
    enrich_entities_parser = subparsers.add_parser(
        "enrich-entities",
        help="从酒馆卡片提取 D&D 实体数据（怪物/物品/技能）"
    )
    enrich_entities_parser.add_argument(
        "--input", "-i", required=True, help="酒馆卡片 JSON 路径"
    )
    enrich_entities_parser.add_argument(
        "--output", "-o", required=True, help="输出目录"
    )
    enrich_entities_parser.add_argument(
        "--model", "-m", default="gemini-3-flash-preview", help="Gemini 模型"
    )

    args = parser.parse_args()

    # 路由到对应命令
    if args.command == "graphize-tavern":
        return asyncio.run(cmd_graphize_tavern(args))
    elif args.command == "extract":
        return asyncio.run(cmd_extract(args))
    elif args.command == "load":
        return asyncio.run(cmd_load(args))
    elif args.command == "verify":
        return asyncio.run(cmd_verify(args))
    elif args.command == "enrich-mainlines":
        return asyncio.run(cmd_enrich_mainlines(args))
    elif args.command == "relabel-edges":
        return asyncio.run(cmd_relabel_edges(args))
    elif args.command == "enrich-entities":
        return asyncio.run(cmd_enrich_entities(args))
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
