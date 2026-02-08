"""
统一世界书提取管线

从 SillyTavern V2 Lorebook JSON 一步生成 WorldInitializer 所需的全部文件：
  maps.json, characters.json, world_map.json, character_profiles.json,
  world_graph.json, prefilled_graph.json, chapters_v2.json,
  monsters.json, items.json, skills.json（--enrich-entities）

所有 LLM 步骤（图谱提取、边重标注、章节增强、实体提取）均支持两种模式：
- Batch API（默认）：50% 成本优惠，需等待排队，全部步骤走 Batch
- 直接调用（--direct）：实时返回，逐条调用 LLM，无成本优惠

注意：部分模型的 Batch API 不支持 thinking_config 字段（如 gemini-3-pro-preview），
此时需加 --thinking-level none 来禁用 thinking，否则 Step 3b 会报 400 错误。

用法:
    # Batch API 模式（默认，含边重标注和实体提取）
    python -m app.tools.init_world_cli extract \
        --input data/gs/worldbook.json \
        --output data/gs/structured/ \
        --model gemini-3-pro-preview \
        --thinking-level none \
        --relabel-edges --enrich-entities

    # 直接调用模式
    python -m app.tools.init_world_cli extract \
        --input data/gs/worldbook.json \
        --output data/gs/structured/ \
        --direct --relabel-edges --enrich-entities
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import re

from google import genai
from google.genai import types

from app.config import settings
from .tavern_card_parser import TavernCardParser
from .map_extractor import MapExtractor
from .npc_classifier import NPCClassifier
from .graph_extractor import GraphExtractor
from .graph_prefill import GraphPrefiller
from .batch_helper import BatchRunner
from .models import (
    CharacterInfo, CharactersData, MapsData, NPCTier,
    WorldMap, WorldMapRegion,
)


class UnifiedWorldExtractor:
    """统一世界书提取编排器"""

    def __init__(
        self,
        model: str = None,
        api_key: str = None,
        verbose: bool = True,
        thinking_level: str = "high",
    ):
        """
        Args:
            model: Gemini 模型名称 (默认: gemini-3-flash-preview)
            api_key: API 密钥
            verbose: 是否输出详细信息
            thinking_level: 思考级别 (lowest/low/medium/high)，用于 Batch API 提取
        """
        self.model = model or settings.gemini_flash_model
        self.api_key = api_key
        self.verbose = verbose
        self.thinking_level = thinking_level

        self.parser = TavernCardParser()
        self.map_extractor = MapExtractor(model=self.model, api_key=self.api_key)
        self.npc_classifier = NPCClassifier(model=self.model, api_key=self.api_key)
        self.graph_extractor = GraphExtractor(
            model=self.model,
            api_key=self.api_key,
            verbose=self.verbose,
            thinking_level=self.thinking_level,
        )
        self.batch_runner = BatchRunner(
            model=self.model,
            api_key=self.api_key,
            verbose=self.verbose,
            log_fn=self._log,
        )

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    async def extract(
        self,
        lorebook_path: Path,
        output_dir: Path,
        mainlines_path: Optional[Path] = None,
        validate: bool = True,
        use_direct: bool = False,
        relabel_edges: bool = False,
        enrich_entities: bool = False,
    ) -> Dict[str, Any]:
        """
        执行统一提取管线

        Args:
            lorebook_path: SillyTavern V2 Lorebook JSON 路径
            output_dir: 输出目录
            mainlines_path: 可选的 mainlines.json 路径
            validate: 是否验证中间结果
            use_direct: 使用直接 LLM 调用而非 Batch API
            relabel_edges: 是否重标注 unknown 边类型
            enrich_entities: 是否提取 D&D 实体数据

        Returns:
            包含统计信息的字典
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        stats: Dict[str, Any] = {"start_time": datetime.now().isoformat()}

        # ── Step 1: 解析酒馆卡片（用于地图/角色提取的 markdown） ──
        self._log("[Step 1] Parsing lorebook JSON...")
        data = self.parser.parse(lorebook_path)
        if self.verbose:
            self.parser.print_summary(data)

        graphable_entries = self.parser.get_graphable_entries(data)
        self._log(f"  Graphable entries: {len(graphable_entries)}")
        stats["total_entries"] = len(data.entries)
        stats["graphable_entries"] = len(graphable_entries)

        # ── Step 2: 格式化全文 markdown（地图和角色提取用） ──
        self._log("\n[Step 2] Formatting worldbook markdown...")
        worldbook_md = self.graph_extractor._format_worldbook_markdown(
            graphable_entries, max_content_chars=8000
        )
        self._log(f"  Markdown size: {len(worldbook_md):,} chars")

        # ── Step 3a: 提取地图 ──
        self._log("\n[Step 3a] Extracting maps...")
        maps_data = await self.map_extractor.extract(worldbook_md)
        self._log(f"  Found {len(maps_data.maps)} maps")

        if validate:
            map_errors = self.map_extractor.validate(maps_data)
            if map_errors:
                self._log(f"  Map warnings: {len(map_errors)}")
                for err in map_errors[:5]:
                    self._log(f"    - {err}")

        # ── Step 3b: 提取知识图谱 ──
        if use_direct:
            self._log(f"\n[Step 3b] Extracting world graph (direct, thinking={self.thinking_level})...")
            self._log(f"  Model: {self.model}")
            graph_data = await self.graph_extractor.extract_direct(
                worldbook_md=worldbook_md,
                entries=graphable_entries,
            )
        else:
            self._log(f"\n[Step 3b] Extracting world graph (Batch API, thinking={self.thinking_level})...")
            self._log(f"  Model: {self.model}")
            batch_temp_dir = output_dir / "batch_temp"
            graph_data = await self.graph_extractor.build_graph(
                json_path=lorebook_path,
                output_dir=batch_temp_dir,
            )
        self._log(f"  Nodes: {len(graph_data.nodes)}, Edges: {len(graph_data.edges)}")
        stats["world_graph_nodes"] = len(graph_data.nodes)
        stats["world_graph_edges"] = len(graph_data.edges)

        # ── Step 4: NPC 分类（依赖 maps） ──
        self._log("\n[Step 4] Classifying NPCs...")
        characters_data = await self.npc_classifier.classify(worldbook_md, maps_data)
        main_count = sum(1 for c in characters_data.characters if c.tier.value == "main")
        secondary_count = sum(1 for c in characters_data.characters if c.tier.value == "secondary")
        passerby_count = sum(1 for c in characters_data.characters if c.tier.value == "passerby")
        self._log(f"  Characters: {len(characters_data.characters)}")
        self._log(f"    main={main_count}, secondary={secondary_count}, passerby={passerby_count}")
        stats["characters"] = len(characters_data.characters)

        if validate:
            char_errors = self.npc_classifier.validate(characters_data, maps_data)
            if char_errors:
                self._log(f"  Character warnings: {len(char_errors)}")
                for err in char_errors[:5]:
                    self._log(f"    - {err}")

        # ── Step 4b: 将 world_graph 中未被 NPC 分类器覆盖的角色回填 ──
        self._log("\n[Step 4b] Reconciling characters from world graph...")
        characters_data = self._reconcile_characters(characters_data, graph_data, maps_data)
        # 更新统计
        main_count = sum(1 for c in characters_data.characters if c.tier.value == "main")
        secondary_count = sum(1 for c in characters_data.characters if c.tier.value == "secondary")
        passerby_count = sum(1 for c in characters_data.characters if c.tier.value == "passerby")
        self._log(f"  Characters after reconciliation: {len(characters_data.characters)}")
        self._log(f"    main={main_count}, secondary={secondary_count}, passerby={passerby_count}")
        stats["characters_after_reconcile"] = len(characters_data.characters)

        # ── Step 5: 生成 world_map.json（纯规则逻辑） ──
        self._log("\n[Step 5] Generating world map...")
        world_map = generate_world_map(maps_data)
        self._log(f"  Regions: {len(world_map.regions)}")

        # ── Step 6: 生成 character_profiles.json ──
        self._log("\n[Step 6] Generating character profiles...")
        profiles = self.npc_classifier.to_character_profiles(characters_data)
        self._log(f"  Profiles: {len(profiles)}")

        # ── Step 7: 保存全部中间文件 ──
        self._log(f"\n[Step 7] Saving files to {output_dir}...")
        _save_json(output_dir / "maps.json", maps_data.model_dump())
        _save_json(output_dir / "characters.json", characters_data.model_dump())
        _save_json(output_dir / "world_map.json", world_map.model_dump())
        _save_json(output_dir / "character_profiles.json", profiles)

        # 保存 world_graph.json（GraphPrefiller 消费）
        def _serialize(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Not serializable: {type(obj)}")

        _save_json(
            output_dir / "world_graph.json",
            graph_data.model_dump(),
            default=_serialize,
        )

        # 保存/生成 mainlines.json
        if mainlines_path and mainlines_path.exists():
            mainlines_data = json.loads(mainlines_path.read_text(encoding="utf-8"))
            # 兼容旧 mainlines.json：缺少 v2 编排字段时自动补齐（Phase 3）
            if self._needs_chapter_orchestration(mainlines_data):
                self._log("\n[Step 7b] Enriching existing mainlines with chapter orchestration...")
                chapters_existing = mainlines_data.get("chapters", [])
                mainlines_existing = mainlines_data.get("mainlines", [])
                volumes: Dict[str, Dict[str, Any]] = {}
                if isinstance(mainlines_existing, list):
                    for mainline in mainlines_existing:
                        if not isinstance(mainline, dict):
                            continue
                        mainline_id = str(mainline.get("id", "")).strip()
                        if mainline_id:
                            volumes[mainline_id] = mainline

                try:
                    if isinstance(chapters_existing, list) and chapters_existing:
                        mainlines_data["chapters"] = await self._extract_chapter_orchestration(
                            chapters_existing,
                            volumes,
                            maps_data,
                            chars_data=characters_data,
                            output_dir=output_dir,
                            use_direct=use_direct,
                        )
                except Exception as exc:
                    self._log(f"  Warning: Existing mainlines orchestration enrichment failed: {exc}")

            # 兜底补齐 v2 必要字段（用于 legacy 数据迁移到 strict-v2）
            self._ensure_v2_story_defaults(mainlines_data)

            if settings.narrative_v2_strict_mode:
                self._validate_mainlines_v2(mainlines_data)

            _save_json(output_dir / "mainlines.json", mainlines_data)
            self._log(f"  Copied mainlines.json ({len(mainlines_data.get('chapters', []))} chapters)")
        else:
            story_entries = self.parser.get_entries_by_types(data, ["story"])
            if story_entries:
                self._log(f"\n[Step 7b] Generating mainlines from {len(story_entries)} story entries...")
                mainlines_data = await self._generate_mainlines(
                    story_entries,
                    maps_data,
                    chars_data=characters_data,
                    output_dir=output_dir, use_direct=use_direct,
                )
                # 兜底补齐 v2 必要字段（用于 legacy 数据迁移到 strict-v2）
                self._ensure_v2_story_defaults(mainlines_data)
                if settings.narrative_v2_strict_mode:
                    self._validate_mainlines_v2(mainlines_data)
                _save_json(output_dir / "mainlines.json", mainlines_data)
                self._log(f"  Generated mainlines.json ({len(mainlines_data.get('mainlines', []))} volumes, {len(mainlines_data.get('chapters', []))} chapters)")
            else:
                self._log("  No story entries found, skipping mainlines generation")

        # ── Step 8: GraphPrefiller ──
        self._log("\n[Step 8] Running GraphPrefiller...")
        prefiller = GraphPrefiller(output_dir)
        prefill_result = prefiller.run(verbose=self.verbose)

        # ── Step 8.5: 可选边重标注 ──
        if relabel_edges:
            self._log("\n[Step 8.5] Relabeling unknown edges...")
            relabel_stats = await self._relabel_unknown_edges(
                prefill_result, output_dir=output_dir, use_direct=use_direct,
            )
            self._log(f"  Relabeled: {relabel_stats['relabeled']}/{relabel_stats['total_unknown']}")
            stats["relabel_edges"] = relabel_stats

            # 同步回写 world_graph.json 中的边
            self._sync_relabeled_edges_to_world_graph(
                output_dir, relabel_stats.get("edge_id_to_relation", {})
            )

        prefiller.save(prefill_result, output_dir)

        stats["prefill_nodes"] = len(prefill_result.nodes)
        stats["prefill_edges"] = len(prefill_result.edges)
        stats["chapters"] = len(prefill_result.chapters_v2)

        # ── Step 9: D&D 实体提取（可选） ──
        if enrich_entities:
            self._log("\n[Step 9] Extracting D&D entities...")
            entity_stats = await self._extract_entities(
                entries=data.entries,
                world_graph_nodes=graph_data.nodes,
                output_dir=output_dir,
                use_direct=use_direct,
            )
            self._log(f"  Monsters: {entity_stats.get('monsters', 0)}, "
                      f"Items: {entity_stats.get('items', 0)}, "
                      f"Skills: {entity_stats.get('skills', 0)}")
            stats["entities"] = entity_stats

        stats["end_time"] = datetime.now().isoformat()

        # ── 完成 ──
        self._log("\n" + "=" * 50)
        self._log("Unified extraction complete!")
        self._log(f"  Output directory: {output_dir}")
        self._log(f"  maps.json:              {len(maps_data.maps)} maps")
        self._log(f"  characters.json:        {len(characters_data.characters)} characters")
        self._log(f"  world_map.json:         {len(world_map.regions)} regions")
        self._log(f"  character_profiles.json: {len(profiles)} profiles")
        self._log(f"  world_graph.json:       {len(graph_data.nodes)} nodes, {len(graph_data.edges)} edges")
        self._log(f"  prefilled_graph.json:   {len(prefill_result.nodes)} nodes, {len(prefill_result.edges)} edges")
        self._log(f"  chapters_v2.json:       {len(prefill_result.chapters_v2)} chapters")
        if enrich_entities and "entities" in stats:
            es = stats["entities"]
            self._log(f"  monsters.json:          {es.get('monsters', 0)} monsters")
            self._log(f"  items.json:             {es.get('items', 0)} items")
            self._log(f"  skills.json:            {es.get('skills', 0)} skills")

        return stats

    # ---- internal helpers ----

    def _reconcile_characters(
        self,
        characters_data: CharactersData,
        graph_data,
        maps_data: MapsData,
    ) -> CharactersData:
        """回填 world_graph 中未被 NPC 分类器覆盖的角色"""
        # 现有角色索引（ID + name）
        existing_ids = {c.id for c in characters_data.characters}
        existing_names = {c.name for c in characters_data.characters}

        # 有效地图 ID 集合
        valid_maps = {m.id for m in maps_data.maps} if maps_data and maps_data.maps else set()

        backfilled = 0
        for node in graph_data.nodes:
            if node.type != "character":
                continue

            # 规范化 ID：strip "character_" prefix
            raw_id = node.id
            normalized_id = raw_id.removeprefix("character_")

            # 跳过已有角色（按 ID 或 name 匹配）
            if normalized_id in existing_ids or raw_id in existing_ids:
                continue
            if node.name in existing_names:
                continue

            # 从 properties 提取信息
            props = node.properties or {}
            description = props.get("description", "")

            # 推断 default_map：从 graph edges 找 located_at 关系
            default_map = None
            if valid_maps:
                for edge in graph_data.edges:
                    if edge.source == raw_id and edge.relation == "located_at":
                        target_map = edge.target.removeprefix("location_")
                        if target_map in valid_maps:
                            default_map = target_map
                            break

            new_char = CharacterInfo(
                id=normalized_id,
                name=node.name,
                tier=NPCTier.SECONDARY,
                default_map=default_map,
                backstory=description,
                importance=node.importance or 0.5,
                tags=["backfilled_from_graph"],
            )
            characters_data.characters.append(new_char)
            existing_ids.add(normalized_id)
            existing_names.add(node.name)
            backfilled += 1

        self._log(f"  Backfilled {backfilled} characters from world_graph")
        return characters_data

    async def _relabel_unknown_edges(
        self,
        prefill_result,
        output_dir: Optional[Path] = None,
        use_direct: bool = False,
    ) -> Dict[str, Any]:
        """重标注 prefill_result 中 relation 为 unknown/related/"" 的边

        Args:
            prefill_result: GraphPrefiller 的输出
            output_dir: 输出目录（batch 模式用于存放临时文件）
            use_direct: True 走逐批 LLM 直接调用，False 走 Batch API
        """
        # 找出需要重标注的边
        unknown_edges = [
            e for e in prefill_result.edges
            if e.relation in ("unknown", "related", "")
        ]
        stats: Dict[str, Any] = {
            "total_unknown": len(unknown_edges),
            "relabeled": 0,
            "edge_id_to_relation": {},
        }

        if not unknown_edges:
            self._log("  No unknown edges found")
            return stats

        # 构建节点查找表
        node_lookup = {n.id: {"name": n.name, "type": n.type} for n in prefill_result.nodes}

        prompt_path = Path(__file__).parent / "prompts" / "edge_relabeling.md"
        if not prompt_path.exists():
            self._log(f"  Warning: Prompt template not found: {prompt_path}")
            return stats

        prompt_template = prompt_path.read_text(encoding="utf-8")
        edge_id_to_relation: Dict[str, str] = {}

        # 分批构建 prompt，每批 30 条
        batch_size = 30
        batch_prompts: List[tuple] = []  # (key, prompt)

        for batch_start in range(0, len(unknown_edges), batch_size):
            batch = unknown_edges[batch_start:batch_start + batch_size]
            batch_num = batch_start // batch_size + 1

            edge_lines = []
            for edge in batch:
                source_info = node_lookup.get(edge.source, {"name": edge.source, "type": "?"})
                target_info = node_lookup.get(edge.target, {"name": edge.target, "type": "?"})
                edge_lines.append(
                    f"- edge_id: {edge.id} | "
                    f"source: {source_info['name']} (type={source_info['type']}) | "
                    f"target: {target_info['name']} (type={target_info['type']})"
                )

            edges_batch = "\n".join(edge_lines)
            prompt = prompt_template.format(edges_batch=edges_batch)
            batch_prompts.append((f"batch_{batch_num}", prompt))

        def _parse_relabel_result(text: str) -> List[Dict]:
            """从原始文本解析边标注结果"""
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                match = re.search(r'[\[\{][\s\S]*[\]\}]', text)
                if match:
                    try:
                        parsed = json.loads(match.group(0))
                    except json.JSONDecodeError:
                        return []
                else:
                    return []

            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and "edges" in parsed:
                return parsed["edges"]
            return []

        if use_direct:
            # ── 直接调用模式 ──
            from app.services.llm_service import LLMService
            llm = LLMService()
            total_batches = len(batch_prompts)

            for key, prompt in batch_prompts:
                self._log(f"    {key}/{total_batches} ...")
                try:
                    result = await llm.generate_simple(prompt, model_override=self.model)
                    items = _parse_relabel_result(result)
                    for item in items:
                        eid = item.get("edge_id", "")
                        rel = item.get("relation", "related_to")
                        if eid:
                            edge_id_to_relation[eid] = rel
                    self._log(f"      Labeled {len(items)} edges")
                except Exception as exc:
                    self._log(f"      Error: {exc}")
        else:
            # ── Batch API 模式 ──
            self._log(f"  Submitting {len(batch_prompts)} batches to Batch API...")
            temp_dir = (output_dir or Path(".")) / "batch_temp"
            raw_results = self.batch_runner.run_batch(
                requests=batch_prompts,
                temp_dir=temp_dir,
                display_name="edge-relabeling",
            )
            for key, text in raw_results.items():
                items = _parse_relabel_result(text)
                for item in items:
                    eid = item.get("edge_id", "")
                    rel = item.get("relation", "related_to")
                    if eid:
                        edge_id_to_relation[eid] = rel

        # 应用标注到 prefill_result.edges
        for edge in prefill_result.edges:
            if edge.id in edge_id_to_relation:
                edge.relation = edge_id_to_relation[edge.id]

        stats["relabeled"] = len(edge_id_to_relation)
        stats["edge_id_to_relation"] = edge_id_to_relation
        return stats

    def _sync_relabeled_edges_to_world_graph(
        self,
        output_dir: Path,
        edge_id_to_relation: Dict[str, str],
    ) -> None:
        """将边重标注结果同步回写到 world_graph.json"""
        if not edge_id_to_relation:
            return

        wg_path = output_dir / "world_graph.json"
        if not wg_path.exists():
            return

        wg_data = json.loads(wg_path.read_text(encoding="utf-8"))
        updated = 0
        for edge in wg_data.get("edges", []):
            eid = edge.get("id", "")
            if eid in edge_id_to_relation:
                edge["relation"] = edge_id_to_relation[eid]
                updated += 1

        if updated:
            _save_json(wg_path, wg_data)
            self._log(f"  Synced {updated} relabeled edges to world_graph.json")

    async def _extract_entities(
        self,
        entries: list,
        world_graph_nodes: list,
        output_dir: Path,
        use_direct: bool = False,
    ) -> Dict[str, int]:
        """从条目中提取 D&D 实体数据（怪物、物品、技能）

        Args:
            use_direct: True 走逐条 LLM 直接调用，False 走 Batch API
        """
        # 构建已有节点属性的查找表
        existing_nodes = {}
        for node in world_graph_nodes:
            existing_nodes[node.id] = {
                "properties": node.properties or {},
            }

        # 分类条目
        monsters_raw, items_raw, skills_raw = [], [], []
        for entry in entries:
            entry_keys = entry.key if isinstance(entry.key, list) else [entry.key] if entry.key else []
            entry_name = entry.comment or ""
            entry_type = entry.entry_type or ""
            entry_group = entry.group or ""

            all_text = f"{entry_name} {' '.join(entry_keys)} {entry_type} {entry_group}".lower()

            if any(kw in all_text for kw in ["monster", "怪物", "魔物", "boss", "敌人"]):
                monsters_raw.append(entry)
            elif any(kw in all_text for kw in ["item", "物品", "武器", "防具", "道具", "装备"]):
                items_raw.append(entry)
            elif any(kw in all_text for kw in ["skill", "技能", "法术", "魔法", "奇迹", "能力"]):
                skills_raw.append(entry)

        self._log(f"  Categorized: {len(monsters_raw)} monsters, {len(items_raw)} items, {len(skills_raw)} skills")

        stats = {"monsters": 0, "items": 0, "skills": 0}

        # 构建 (category_name, prompt_file, entries) 三元组
        categories = [
            ("monster", "monster_extraction.md", monsters_raw),
            ("item", "item_extraction.md", items_raw),
            ("skill", "skill_extraction.md", skills_raw),
        ]

        def _build_entity_prompts(category_name, prompt_file, category_entries):
            """为一个类别的全部条目构建 (key, prompt) 列表"""
            prompt_path = Path(__file__).parent / "prompts" / prompt_file
            if not prompt_path.exists():
                self._log(f"  Warning: {prompt_path} not found, skipping {category_name}")
                return []

            prompt_template = prompt_path.read_text(encoding="utf-8")
            prompts = []

            for i, entry in enumerate(category_entries):
                entry_name = entry.comment or ""
                entry_id = entry_name.replace(" ", "_").lower() or f"{category_name}_{i}"
                entry_desc = (entry.content or "")[:3000]

                existing = existing_nodes.get(entry_id, {})
                existing_props = existing.get("properties", {}) if existing else {}
                existing_text = json.dumps(existing_props, ensure_ascii=False) if existing_props else "无"

                prompt = prompt_template.format(
                    **{
                        f"{category_name}_id": entry_id,
                        f"{category_name}_name": entry_name,
                        f"{category_name}_description": entry_desc,
                        "existing_properties": existing_text,
                    }
                )
                prompts.append((f"{category_name}_{i}", prompt))

            return prompts

        def _parse_entity_text(text: str):
            """解析单个实体的 JSON 结果"""
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                match = re.search(r'\{[\s\S]*\}', text)
                if match:
                    try:
                        return json.loads(match.group(0))
                    except json.JSONDecodeError:
                        return None
                return None

        if use_direct:
            # ── 直接调用模式：逐条处理 ──
            from app.services.llm_service import LLMService
            llm = LLMService()

            for category_name, prompt_file, category_entries in categories:
                if not category_entries:
                    continue
                self._log(f"\n  Processing {len(category_entries)} {category_name}s (direct)...")
                prompts = _build_entity_prompts(category_name, prompt_file, category_entries)
                results = []
                for i, (key, prompt) in enumerate(prompts):
                    entry_name = category_entries[i].comment or key
                    self._log(f"    [{i+1}/{len(prompts)}] {entry_name}...")
                    try:
                        result = await llm.generate_simple(prompt, model_override=self.model)
                        parsed = llm.parse_json(result)
                        if parsed:
                            results.append(parsed)
                        else:
                            self._log(f"      Parse failed for {entry_name}")
                    except Exception as exc:
                        self._log(f"      Error: {exc}")

                plural = f"{category_name}s"
                _save_json(output_dir / f"{plural}.json", {plural: results})
                stats[plural] = len(results)
        else:
            # ── Batch API 模式：所有类别合并为一个 batch ──
            all_prompts: List[tuple] = []
            category_ranges: Dict[str, int] = {}  # category_name -> count

            for category_name, prompt_file, category_entries in categories:
                if not category_entries:
                    continue
                prompts = _build_entity_prompts(category_name, prompt_file, category_entries)
                category_ranges[category_name] = len(prompts)
                all_prompts.extend(prompts)

            if all_prompts:
                self._log(f"\n  Submitting {len(all_prompts)} entity extractions to Batch API...")
                temp_dir = output_dir / "batch_temp"
                raw_results = self.batch_runner.run_batch(
                    requests=all_prompts,
                    temp_dir=temp_dir,
                    display_name="entity-extraction",
                )

                # 按 key 前缀拆分结果
                for category_name, _, _ in categories:
                    plural = f"{category_name}s"
                    count = category_ranges.get(category_name, 0)
                    if count == 0:
                        continue
                    results = []
                    for i in range(count):
                        key = f"{category_name}_{i}"
                        text = raw_results.get(key, "")
                        if text:
                            parsed = _parse_entity_text(text)
                            if parsed:
                                results.append(parsed)

                    _save_json(output_dir / f"{plural}.json", {plural: results})
                    stats[plural] = len(results)

        return stats

    async def _generate_mainlines(
        self,
        story_entries: list,
        maps_data: MapsData,
        chars_data: Any = None,
        output_dir: Optional[Path] = None,
        use_direct: bool = False,
    ) -> Dict[str, Any]:
        """从故事类条目生成 mainlines.json

        Phase 1（纯规则）：从 entry.comment 提取卷/章结构 + 章节分类 + 正则提取
        Phase 2（LLM）：增量填充缺失的 available_maps、objectives 等
        """
        # Phase 1: 用正则提取卷/章结构
        volumes: Dict[str, Dict[str, Any]] = {}   # vol_id -> {name, chapters: []}
        chapters: List[Dict[str, Any]] = []

        # 对 story_entries 按 comment 排序确保顺序
        sorted_entries = sorted(story_entries, key=lambda e: e.order)

        vol_pattern = re.compile(r'第(\d+|[一二三四五六七八九十百]+)卷')
        ch_pattern = re.compile(r'第(\d+|[一二三四五六七八九十百]+)章')

        # 章节分类正则
        metadata_keywords = ["状态栏", "剧情系统", "剧情初始化", "章节管理器"]
        volume_idx_pattern = re.compile(r"^[📖📚\s]*第[一二三四五六七八九十\d]+卷[^章]*$")

        # 正则提取目标和事件
        objective_pattern = re.compile(r"(?:主要目标|章节目标)[：:]\s*(.+?)(?:\n|$)")
        event_list_pattern = re.compile(r"<第\d+章事件列表>(.*?)</", re.DOTALL)
        event_line_pattern = re.compile(r"章节事件[：:]\s*(.+?)(?:\n|$)")

        current_vol_id = "vol_1"
        current_vol_name = "第一卷"
        classify_stats = {"metadata": 0, "volume_index": 0, "story": 0}

        for entry in sorted_entries:
            comment = entry.comment or ""
            content_preview = (entry.content or "")[:500]

            # ── 章节类型分类 ──
            if any(kw in comment for kw in metadata_keywords):
                classify_stats["metadata"] += 1
                continue  # 跳过元数据条目

            comment_stripped = re.sub(r"[📖📚\s]", "", comment)
            if volume_idx_pattern.match(comment) or (
                re.match(r"第[一二三四五六七八九十\d]+卷", comment_stripped)
                and "章" not in comment_stripped
            ):
                # 仅作为卷标题，更新卷信息但不生成章节
                vol_match = vol_pattern.search(comment)
                if vol_match:
                    vol_num = vol_match.group(1)
                    if vol_num.isdigit():
                        vol_num_int = int(vol_num)
                    else:
                        vol_num_int = self._cn_num_to_int(vol_num)
                    current_vol_id = f"vol_{vol_num_int}"
                    current_vol_name = comment.split(" - ")[0].strip() if " - " in comment else comment.strip()
                    if current_vol_id not in volumes:
                        volumes[current_vol_id] = {
                            "id": current_vol_id,
                            "name": current_vol_name,
                            "description": content_preview,
                            "chapters": [],
                        }
                classify_stats["volume_index"] += 1
                continue

            # 故事章节
            ch_type = "story"
            classify_stats["story"] += 1

            # 检测卷号
            vol_match = vol_pattern.search(comment)
            if vol_match:
                vol_num = vol_match.group(1)
                # 数字化卷号
                if vol_num.isdigit():
                    vol_num_int = int(vol_num)
                else:
                    vol_num_int = self._cn_num_to_int(vol_num)
                current_vol_id = f"vol_{vol_num_int}"
                current_vol_name = comment.split(" - ")[0].strip() if " - " in comment else comment.strip()

            # 检测章号
            ch_match = ch_pattern.search(comment)
            if ch_match:
                ch_num = ch_match.group(1)
                if ch_num.isdigit():
                    ch_num_int = int(ch_num)
                else:
                    ch_num_int = self._cn_num_to_int(ch_num)
            else:
                # 无章号标记，按序编号
                ch_num_int = len(chapters) + 1

            # 提取章节名称
            ch_name = comment.strip()
            if " - " in comment:
                ch_name = comment.split(" - ", 1)[1].strip()

            # 确保卷存在
            if current_vol_id not in volumes:
                volumes[current_vol_id] = {
                    "id": current_vol_id,
                    "name": current_vol_name,
                    "description": "",
                    "chapters": [],
                }

            # 提取卷号数字用于章节 ID（确保唯一性）
            vol_num_str = current_vol_id.removeprefix("vol_")
            ch_id = f"ch_{vol_num_str}_{ch_num_int}"
            existing_ids = {c["id"] for c in chapters}
            if ch_id in existing_ids:
                suffix = 2
                while f"{ch_id}_{suffix}" in existing_ids:
                    suffix += 1
                self._log(f"    ID collision: {ch_id} → {ch_id}_{suffix}")
                ch_id = f"{ch_id}_{suffix}"

            # ── 正则提取目标 ──
            objectives = []
            for match in objective_pattern.finditer(content_preview):
                obj_text = match.group(1).strip()
                if obj_text:
                    objectives.append(obj_text)

            # ── 正则提取事件 ──
            events = []
            for match in event_list_pattern.finditer(content_preview):
                event_block = match.group(1)
                for line in event_block.strip().splitlines():
                    line = line.strip(" -*·")
                    if line:
                        events.append(f"{ch_id}_{line.replace(' ', '_')[:30]}")

            for match in event_line_pattern.finditer(content_preview):
                event_text = match.group(1).strip()
                if event_text:
                    for ev in re.split(r"[,，、;；]", event_text):
                        ev = ev.strip()
                        if ev:
                            events.append(f"{ch_id}_{ev.replace(' ', '_')[:30]}")

            completion_conditions = {}
            if events:
                completion_conditions["events_required"] = events

            chapter_info = {
                "id": ch_id,
                "mainline_id": current_vol_id,
                "name": ch_name,
                "type": ch_type,
                "description": content_preview,
                "available_maps": [],
                "objectives": objectives,
                "trigger_conditions": {},
                "completion_conditions": completion_conditions,
            }
            chapters.append(chapter_info)
            volumes[current_vol_id]["chapters"].append(ch_id)

        self._log(f"  Classification: metadata={classify_stats['metadata']}, "
                  f"volume_index={classify_stats['volume_index']}, story={classify_stats['story']}")

        # Phase 2: 增量 LLM 填充 available_maps 和 objectives
        if chapters and maps_data and maps_data.maps:
            try:
                chapters = await self._enrich_mainlines_incremental(
                    chapters, volumes, maps_data,
                    output_dir=output_dir, use_direct=use_direct,
                )
            except Exception as e:
                import traceback
                self._log(f"  Warning: Incremental LLM enrichment failed, trying batch fallback: {e}")
                self._log(f"  Traceback: {traceback.format_exc()}")
                if settings.narrative_v2_strict_mode:
                    raise
                try:
                    chapters = await self._enrich_mainlines_with_llm(
                        chapters, volumes, maps_data
                    )
                except Exception as e2:
                    self._log(f"  Warning: Batch LLM enrichment also failed: {e2}")

        # Phase 3: 章节编排提取（events, transitions, pacing）
        if chapters and maps_data:
            try:
                chapters = await self._extract_chapter_orchestration(
                    chapters, volumes, maps_data, chars_data=chars_data,
                    output_dir=output_dir, use_direct=use_direct,
                )
            except Exception as e:
                import traceback
                self._log(f"  ERROR: Chapter orchestration extraction failed: {e}")
                self._log(f"  Traceback: {traceback.format_exc()}")
                if settings.narrative_v2_strict_mode:
                    raise

        mainlines_list = list(volumes.values())
        return {
            "mainlines": mainlines_list,
            "chapters": chapters,
        }

    async def _enrich_mainlines_with_llm(
        self,
        chapters: List[Dict[str, Any]],
        volumes: Dict[str, Dict[str, Any]],
        maps_data: MapsData,
    ) -> List[Dict[str, Any]]:
        """用 LLM 为每个章节填充 available_maps 和 objectives"""
        prompt_path = Path(__file__).parent / "prompts" / "mainline_extraction.md"
        prompt_template = prompt_path.read_text(encoding="utf-8")

        known_maps = "\n".join(f"- {m.id}: {m.name}" for m in maps_data.maps)

        # 构建章节输入摘要
        chapters_input_parts = []
        for ch in chapters:
            chapters_input_parts.append(
                f"### {ch['id']} ({ch['mainline_id']}): {ch['name']}\n"
                f"{ch['description'][:300]}"
            )
        chapters_input = "\n\n".join(chapters_input_parts)

        prompt = prompt_template.replace(
            "{known_maps}", known_maps
        ).replace(
            "{chapters_input}", chapters_input
        )

        client = genai.Client(api_key=self.api_key or settings.gemini_api_key)
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
            max_output_tokens=65536,
        )

        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )

        # 提取 JSON
        text = ""
        if hasattr(response, 'candidates') and response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'text') and part.text:
                    if not (hasattr(part, 'thought') and part.thought):
                        text += part.text

        if not text:
            return chapters

        try:
            enriched = json.loads(text)
        except json.JSONDecodeError:
            # 尝试提取 JSON 块
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                enriched = json.loads(match.group(0))
            else:
                return chapters

        # 合并 LLM 结果到章节
        llm_chapters = {ch["id"]: ch for ch in enriched.get("chapters", [])}
        valid_map_ids = {m.id for m in maps_data.maps}

        for ch in chapters:
            if ch["id"] in llm_chapters:
                llm_ch = llm_chapters[ch["id"]]
                # 只保留有效地图 ID
                ch["available_maps"] = [
                    m for m in llm_ch.get("available_maps", [])
                    if m in valid_map_ids
                ]
                ch["objectives"] = llm_ch.get("objectives", [])
                ch["trigger_conditions"] = llm_ch.get("trigger_conditions", {})
                ch["completion_conditions"] = llm_ch.get("completion_conditions", {})

        # 合并卷级信息
        llm_mainlines = {ml["id"]: ml for ml in enriched.get("mainlines", [])}
        for vol_id, vol in volumes.items():
            if vol_id in llm_mainlines:
                vol["description"] = llm_mainlines[vol_id].get("description", vol["description"])

        return chapters

    async def _enrich_mainlines_incremental(
        self,
        chapters: List[Dict[str, Any]],
        volumes: Dict[str, Dict[str, Any]],
        maps_data: MapsData,
        output_dir: Optional[Path] = None,
        use_direct: bool = False,
    ) -> List[Dict[str, Any]]:
        """增量 LLM 增强：只处理 type==story 且缺失字段的章节

        Args:
            output_dir: 输出目录（batch 模式用于存放临时文件）
            use_direct: True 走逐章 LLM 直接调用，False 走 Batch API
        """
        prompt_path = Path(__file__).parent / "prompts" / "mainline_enrichment.md"
        if not prompt_path.exists():
            self._log(f"  Warning: Prompt template not found: {prompt_path}")
            return chapters

        prompt_template = prompt_path.read_text(encoding="utf-8")
        known_maps = "\n".join(f"- {m.id}: {m.name}" for m in maps_data.maps)
        valid_map_ids = {m.id for m in maps_data.maps}

        # 筛选需要 LLM 增强的章节
        need_enrich = [
            ch for ch in chapters
            if ch.get("type") == "story"
            and (not ch.get("available_maps") or not ch.get("objectives"))
        ]

        if not need_enrich:
            self._log("  All story chapters already have maps and objectives, skipping LLM")
            return chapters

        self._log(f"  Chapters needing LLM enrichment: {len(need_enrich)}/{len(chapters)}")

        # 构建所有章节的 prompt
        chapter_prompts: List[tuple] = []  # (key, prompt)
        for ch in need_enrich:
            ch_id = ch.get("id", "unknown")

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
            chapter_prompts.append((f"ch_{ch_id}", prompt))

        def _apply_enrichment(ch: Dict[str, Any], parsed: Dict[str, Any]) -> bool:
            """将 LLM 结果合并到章节，返回是否成功"""
            if not parsed:
                return False
            if parsed.get("available_maps") and not ch.get("available_maps"):
                ch["available_maps"] = [
                    m for m in parsed["available_maps"]
                    if m in valid_map_ids
                ]
            if parsed.get("objectives") and not ch.get("objectives"):
                ch["objectives"] = parsed["objectives"]
            if parsed.get("completion_conditions"):
                if "completion_conditions" not in ch:
                    ch["completion_conditions"] = {}
                cc = parsed["completion_conditions"]
                if cc.get("events_required") and not ch["completion_conditions"].get("events_required"):
                    ch["completion_conditions"]["events_required"] = cc["events_required"]
            return True

        enriched_count = 0

        if use_direct:
            # ── 直接调用模式 ──
            from app.services.llm_service import LLMService
            llm = LLMService()

            for i, (ch, (key, prompt)) in enumerate(zip(need_enrich, chapter_prompts)):
                ch_id = ch.get("id", "unknown")
                self._log(f"    [{i+1}/{len(need_enrich)}] Enriching {ch_id}...")
                try:
                    result = await llm.generate_simple(prompt, model_override=self.model)
                    parsed = llm.parse_json(result)
                    if _apply_enrichment(ch, parsed):
                        enriched_count += 1
                    else:
                        self._log(f"      Parse failed for {ch_id}")
                except Exception as exc:
                    self._log(f"      Error enriching {ch_id}: {exc}")
        else:
            # ── Batch API 模式 ──
            self._log(f"  Submitting {len(chapter_prompts)} chapters to Batch API...")
            temp_dir = (output_dir or Path(".")) / "batch_temp"
            raw_results = self.batch_runner.run_batch(
                requests=chapter_prompts,
                temp_dir=temp_dir,
                display_name="mainline-enrichment",
            )

            # 按 key 匹配回章节（防御性去重）
            key_to_ch: Dict[str, Dict] = {}
            for ch in need_enrich:
                key = f"ch_{ch.get('id', 'unknown')}"
                if key in key_to_ch:
                    self._log(f"    WARNING: Duplicate enrichment key {key}, skipping")
                    continue
                key_to_ch[key] = ch
            for key, text in raw_results.items():
                ch = key_to_ch.get(key)
                if not ch:
                    continue
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    match = re.search(r'\{[\s\S]*\}', text)
                    if match:
                        try:
                            parsed = json.loads(match.group(0))
                        except json.JSONDecodeError:
                            continue
                    else:
                        continue

                if _apply_enrichment(ch, parsed):
                    enriched_count += 1

        self._log(f"  LLM enriched: {enriched_count}/{len(need_enrich)}")
        return chapters

    async def _extract_chapter_orchestration(
        self,
        chapters: List[Dict[str, Any]],
        volumes: Dict[str, Dict[str, Any]],
        maps_data: MapsData,
        chars_data: Any = None,
        output_dir: Optional[Path] = None,
        use_direct: bool = False,
    ) -> List[Dict[str, Any]]:
        """Phase 3: 从章节描述提取剧情编排数据（events, transitions, pacing）

        Args:
            chapters: 章节列表
            volumes: 卷字典
            maps_data: 地图数据
            chars_data: 角色数据（可选）
            output_dir: 输出目录（batch 模式临时文件）
            use_direct: True 走直接调用，False 走 Batch API
        """
        prompt_path = Path(__file__).parent / "prompts" / "chapter_orchestration.md"
        if not prompt_path.exists():
            self._log(f"  Warning: Prompt template not found: {prompt_path}")
            return chapters

        prompt_template = prompt_path.read_text(encoding="utf-8")

        # 筛选 story 类型的章节
        story_chapters = [ch for ch in chapters if ch.get("type") == "story"]
        if not story_chapters:
            self._log("  No story chapters for orchestration extraction")
            return chapters

        self._log(f"\n  [Phase 3] Extracting chapter orchestration for {len(story_chapters)} chapters...")

        # 构建已知地图和 NPC 列表
        known_maps = "\n".join(f"- {m.id}: {m.name}" for m in maps_data.maps) if maps_data and maps_data.maps else "无"
        known_npcs = "无"
        if chars_data and hasattr(chars_data, "characters"):
            known_npcs = "\n".join(
                f"- {c.id}: {c.name}" for c in chars_data.characters[:50]
            ) or "无"

        # 按章节顺序构建 prompt，跟踪前序事件
        chapter_prompts: List[tuple] = []  # (key, prompt)
        previous_events_by_chapter: Dict[str, str] = {}

        # 预计算每个章节的前序章节 ID
        chapter_order = {ch["id"]: i for i, ch in enumerate(story_chapters)}
        prev_chapter_ids: Dict[str, Optional[str]] = {}
        for i, ch in enumerate(story_chapters):
            if i > 0:
                prev_chapter_ids[ch["id"]] = story_chapters[i - 1]["id"]
            else:
                prev_chapter_ids[ch["id"]] = None

        for ch in story_chapters:
            ch_id = ch.get("id", "unknown")
            prev_id = prev_chapter_ids.get(ch_id)

            # 构建前序事件信息
            if prev_id and prev_id in previous_events_by_chapter:
                previous_events = previous_events_by_chapter[prev_id]
            elif prev_id:
                # 用前序章节的事件作为参考
                prev_ch = next((c for c in chapters if c.get("id") == prev_id), None)
                if prev_ch and prev_ch.get("completion_conditions", {}).get("events_required"):
                    events_list = prev_ch["completion_conditions"]["events_required"]
                    previous_events = "\n".join(f"- {eid}" for eid in events_list)
                else:
                    previous_events = f"- {prev_id}_event_final（前一章最终事件）"
            else:
                previous_events = "无（这是第一章）"

            prompt = prompt_template.format(
                chapter_id=ch_id,
                chapter_name=ch.get("name", ""),
                chapter_description=ch.get("description", "")[:3000],
                known_maps=known_maps,
                known_npcs=known_npcs,
                previous_events=previous_events,
            )
            orch_key = f"orch_{ch_id}"
            existing_keys = {k for k, _ in chapter_prompts}
            if orch_key in existing_keys:
                self._log(f"    WARNING: Duplicate orchestration key {orch_key}, appending suffix")
                orch_key = f"{orch_key}__dup"
            chapter_prompts.append((orch_key, prompt))

            # 为后续章节预填前序事件占位
            previous_events_by_chapter[ch_id] = f"- {ch_id}_event_1（将由 LLM 生成）"

        def _parse_orchestration(text: str) -> Optional[Dict[str, Any]]:
            """解析编排 JSON 结果"""
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                match = re.search(r'\{[\s\S]*\}', text)
                if match:
                    try:
                        return json.loads(match.group(0))
                    except json.JSONDecodeError:
                        return None
                return None

        def _apply_orchestration(ch: Dict[str, Any], parsed: Dict[str, Any]) -> bool:
            """将编排结果合并到章节"""
            if not parsed:
                return False
            events_raw = parsed.get("events", [])
            transitions_raw = parsed.get("transitions", [])
            pacing_raw = parsed.get("pacing", {})

            ch["events"] = events_raw if isinstance(events_raw, list) else []
            ch["transitions"] = transitions_raw if isinstance(transitions_raw, list) else []
            ch["pacing"] = pacing_raw if isinstance(pacing_raw, dict) else {}
            ch["entry_conditions"] = parsed.get("entry_conditions")
            tags_raw = parsed.get("tags", [])
            ch["tags"] = tags_raw if isinstance(tags_raw, list) else []
            return True

        orchestrated_count = 0
        ch_id_to_chapter: Dict[str, Dict] = {}
        for ch in chapters:
            cid = ch["id"]
            if cid in ch_id_to_chapter:
                self._log(f"    WARNING: Duplicate chapter ID {cid} in orchestration mapping")
                continue
            ch_id_to_chapter[cid] = ch

        if use_direct:
            # ── 直接调用模式 ──
            from app.services.llm_service import LLMService
            llm = LLMService()

            for i, (key, prompt) in enumerate(chapter_prompts):
                ch_id = key.removeprefix("orch_")
                ch = ch_id_to_chapter.get(ch_id)
                if not ch:
                    continue
                self._log(f"    [{i+1}/{len(chapter_prompts)}] Orchestrating {ch_id}...")
                try:
                    result = await llm.generate_simple(prompt, model_override=self.model)
                    parsed = _parse_orchestration(result)
                    if _apply_orchestration(ch, parsed):
                        orchestrated_count += 1
                    else:
                        self._log(f"      Parse failed for {ch_id}")
                except Exception as exc:
                    self._log(f"      Error: {exc}")
        else:
            # ── Batch API 模式 ──
            self._log(f"  Submitting {len(chapter_prompts)} chapters to Batch API for orchestration...")
            temp_dir = (output_dir or Path(".")) / "batch_temp"
            raw_results = self.batch_runner.run_batch(
                requests=chapter_prompts,
                temp_dir=temp_dir,
                display_name="chapter-orchestration",
            )

            for key, text in raw_results.items():
                ch_id = key.removeprefix("orch_")
                ch = ch_id_to_chapter.get(ch_id)
                if not ch:
                    continue
                parsed = _parse_orchestration(text)
                if _apply_orchestration(ch, parsed):
                    orchestrated_count += 1

        # strict-v2: 即使解析失败也写入空键，避免下游字段缺失
        for ch in story_chapters:
            ch.setdefault("events", [])
            ch.setdefault("transitions", [])
            ch.setdefault("pacing", {})
            ch.setdefault("entry_conditions", None)
            ch.setdefault("tags", [])

        self._log(f"  Chapter orchestration extracted: {orchestrated_count}/{len(story_chapters)}")
        return chapters

    @staticmethod
    def _build_linear_chapter_graph(chapter_ids: List[str]) -> Dict[str, List[str]]:
        """按章节顺序构建线性 chapter_graph。"""
        graph: Dict[str, List[str]] = {}
        for i in range(len(chapter_ids) - 1):
            graph[chapter_ids[i]] = [chapter_ids[i + 1]]
        return graph

    @classmethod
    def _synthesize_story_events(cls, chapter: Dict[str, Any]) -> None:
        """从 completion_conditions 机械生成最小可用 v2 events。"""
        chapter_id = str(chapter.get("id") or "unknown").strip() or "unknown"

        completion = chapter.get("completion_conditions")
        if not isinstance(completion, dict):
            completion = {}
            chapter["completion_conditions"] = completion

        required_raw = completion.get("events_required")
        required_events: List[str] = []
        if isinstance(required_raw, list):
            for event_id in required_raw:
                if isinstance(event_id, str) and event_id.strip():
                    required_events.append(event_id.strip())
        if not required_events:
            required_events = [f"{chapter_id}_event_1"]

        synthesized_events: List[Dict[str, Any]] = []
        prev_event_id: Optional[str] = None
        for event_id in required_events:
            conditions = []
            if prev_event_id:
                conditions.append({
                    "type": "event_triggered",
                    "params": {"event_id": prev_event_id},
                })
            else:
                # 第一事件设置为回合0即可触发，保证 strict-v2 下章节可推进
                conditions.append({
                    "type": "rounds_elapsed",
                    "params": {"min_rounds": 0},
                })

            synthesized_events.append({
                "id": event_id,
                "name": event_id,
                "description": "Auto-synthesized v2 event from legacy completion_conditions",
                "is_required": True,
                "is_repeatable": False,
                "cooldown_rounds": 0,
                "trigger_conditions": {
                    "operator": "and",
                    "conditions": conditions,
                },
                "narrative_directive": "",
                "side_effects": [],
            })
            prev_event_id = event_id

        chapter["events"] = synthesized_events
        completion["events_required"] = required_events

        tags_raw = chapter.get("tags")
        tags = tags_raw if isinstance(tags_raw, list) else []
        if "auto_migrated_v2" not in tags:
            tags.append("auto_migrated_v2")
        chapter["tags"] = tags

    @classmethod
    def _ensure_v2_story_defaults(cls, mainlines_data: Dict[str, Any]) -> None:
        """补齐 strict-v2 需要的章节和主线字段。"""
        chapters = mainlines_data.get("chapters")
        if not isinstance(chapters, list):
            return

        valid_chapter_ids: List[str] = []
        chapters_by_mainline: Dict[str, List[str]] = {}
        for chapter in chapters:
            if not isinstance(chapter, dict):
                continue

            chapter_id = str(chapter.get("id") or "").strip()
            if chapter_id:
                valid_chapter_ids.append(chapter_id)

            mainline_id = str(chapter.get("mainline_id") or "").strip()
            if mainline_id and chapter_id:
                chapters_by_mainline.setdefault(mainline_id, []).append(chapter_id)

            chapter_type = str(chapter.get("type") or "story").strip().lower()
            if chapter_type != "story":
                continue

            events_raw = chapter.get("events")
            if not isinstance(events_raw, list) or not events_raw:
                cls._synthesize_story_events(chapter)

            if not isinstance(chapter.get("transitions"), list):
                chapter["transitions"] = []
            if not isinstance(chapter.get("pacing"), dict):
                chapter["pacing"] = {
                    "min_rounds": 3,
                    "ideal_rounds": 10,
                    "max_rounds": 30,
                    "stall_threshold": 5,
                    "hint_escalation": [
                        "subtle_environmental",
                        "npc_reminder",
                        "direct_prompt",
                        "forced_event",
                    ],
                }
            if "entry_conditions" not in chapter:
                chapter["entry_conditions"] = None
            if not isinstance(chapter.get("tags"), list):
                chapter["tags"] = []

            # 若 completion_conditions 缺失 events_required，回填 required event ids
            completion = chapter.get("completion_conditions")
            if not isinstance(completion, dict):
                completion = {}
                chapter["completion_conditions"] = completion
            required = completion.get("events_required")
            if not isinstance(required, list) or not required:
                required_ids: List[str] = []
                for ev in chapter.get("events", []):
                    if not isinstance(ev, dict):
                        continue
                    event_id = str(ev.get("id") or "").strip()
                    if not event_id:
                        continue
                    if ev.get("is_required", True):
                        required_ids.append(event_id)
                if not required_ids and chapter.get("events"):
                    first = chapter["events"][0]
                    if isinstance(first, dict):
                        first_id = str(first.get("id") or "").strip()
                        if first_id:
                            required_ids = [first_id]
                if required_ids:
                    completion["events_required"] = required_ids

        valid_set = set(valid_chapter_ids)
        mainlines = mainlines_data.get("mainlines")
        if not isinstance(mainlines, list):
            return

        for mainline in mainlines:
            if not isinstance(mainline, dict):
                continue
            mainline_id = str(mainline.get("id") or "").strip()

            chapters_raw = mainline.get("chapters")
            normalized_chapters: List[str] = []
            if isinstance(chapters_raw, list):
                for cid in chapters_raw:
                    if isinstance(cid, str) and cid.strip() and cid.strip() in valid_set:
                        normalized_chapters.append(cid.strip())
            if not normalized_chapters and mainline_id in chapters_by_mainline:
                normalized_chapters = list(chapters_by_mainline[mainline_id])
            # 去重保序
            normalized_chapters = list(dict.fromkeys(normalized_chapters))
            mainline["chapters"] = normalized_chapters

            # Transitions-first DAG 构建策略
            # 1. 从章节的 transitions 字段构建 DAG
            transition_graph: Dict[str, List[str]] = {}
            chapters_data = mainlines_data.get("chapters", [])
            for chapter in chapters_data:
                if not isinstance(chapter, dict):
                    continue
                ch_id = str(chapter.get("id", "")).strip()
                if ch_id not in valid_set:
                    continue
                transitions = chapter.get("transitions", [])
                if isinstance(transitions, list):
                    targets = []
                    for trans in transitions:
                        if isinstance(trans, dict):
                            target = str(trans.get("target_chapter_id", "")).strip()
                            if target and target in valid_set:
                                targets.append(target)
                    if targets:
                        transition_graph[ch_id] = list(dict.fromkeys(targets))

            # 2. 合并已有 chapter_graph（如果存在）
            graph_raw = mainline.get("chapter_graph")
            if isinstance(graph_raw, dict) and graph_raw:
                for src, targets in graph_raw.items():
                    src_id = str(src).strip()
                    if not src_id or src_id not in valid_set:
                        continue
                    if not isinstance(targets, list):
                        continue
                    valid_targets = [
                        t.strip() for t in targets
                        if isinstance(t, str) and t.strip() in valid_set
                    ]
                    if valid_targets:
                        existing = transition_graph.get(src_id, [])
                        merged = list(dict.fromkeys(existing + valid_targets))
                        transition_graph[src_id] = merged

            # 3. 对 mainline 内缺失的节点补线性边
            for i, ch_id in enumerate(normalized_chapters[:-1]):
                if ch_id not in transition_graph:
                    transition_graph[ch_id] = [normalized_chapters[i + 1]]

            if transition_graph:
                mainline["chapter_graph"] = transition_graph
            else:
                mainline["chapter_graph"] = cls._build_linear_chapter_graph(normalized_chapters)

    @staticmethod
    def _cn_num_to_int(cn: str) -> int:
        """简易中文数字转整数"""
        cn_map = {
            "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
            "百": 100,
        }
        if len(cn) == 1:
            return cn_map.get(cn, 1)
        # 处理 "十一" ~ "十九"
        if cn.startswith("十"):
            return 10 + cn_map.get(cn[1:], 0)
        # 处理 "二十" ~ "九十九"
        if "十" in cn:
            parts = cn.split("十")
            tens = cn_map.get(parts[0], 0) * 10
            ones = cn_map.get(parts[1], 0) if parts[1] else 0
            return tens + ones
        return cn_map.get(cn, 1)

    @staticmethod
    def _needs_chapter_orchestration(mainlines_data: Dict[str, Any]) -> bool:
        """判断 mainlines.json 是否缺失 v2 章节编排字段。"""
        chapters = mainlines_data.get("chapters")
        if not isinstance(chapters, list):
            return False

        for chapter in chapters:
            if not isinstance(chapter, dict):
                continue
            if chapter.get("type") != "story":
                continue
            has_events = bool(chapter.get("events"))
            has_transitions = bool(chapter.get("transitions"))
            has_pacing = bool(chapter.get("pacing"))
            if not (has_events or has_transitions or has_pacing):
                return True

        return False

    @staticmethod
    def _validate_mainlines_v2(mainlines_data: Dict[str, Any]) -> None:
        """strict-v2 校验：story 章节必须携带可用编排字段。"""
        chapters = mainlines_data.get("chapters")
        if not isinstance(chapters, list):
            raise ValueError("mainlines.json 缺少 chapters 列表")

        # 5a. chapter_id 唯一性校验
        all_ids = [ch.get("id") for ch in chapters if isinstance(ch, dict)]
        duplicates = [cid for cid in all_ids if all_ids.count(cid) > 1]
        if duplicates:
            raise ValueError(
                f"strict-v2 校验失败: 存在重复 chapter_id: {set(duplicates)}"
            )

        for chapter in chapters:
            if not isinstance(chapter, dict):
                continue
            if chapter.get("type") != "story":
                continue
            chapter_id = chapter.get("id", "unknown")
            if not isinstance(chapter.get("events"), list) or not chapter.get("events"):
                raise ValueError(f"strict-v2 校验失败: chapter={chapter_id} 缺少有效 events")
            if not isinstance(chapter.get("transitions"), list):
                raise ValueError(f"strict-v2 校验失败: chapter={chapter_id} transitions 非列表")
            if not isinstance(chapter.get("pacing"), dict):
                raise ValueError(f"strict-v2 校验失败: chapter={chapter_id} pacing 非对象")

        # 5b. 真实提取 vs 兜底合成 质量门控
        story_chapters = [
            ch for ch in chapters
            if isinstance(ch, dict) and ch.get("type") == "story"
        ]
        auto_migrated_count = sum(
            1 for ch in story_chapters
            if "auto_migrated_v2" in (ch.get("tags") or [])
        )
        auto_ratio = auto_migrated_count / max(len(story_chapters), 1)
        if auto_ratio > 0.5:
            raise ValueError(
                f"strict-v2 质量校验失败: {auto_migrated_count}/{len(story_chapters)} "
                f"story 章节仍为兜底合成 (auto_migrated_v2)，"
                f"比例 {auto_ratio:.0%} > 50%，请检查 Phase 3 orchestration 是否成功运行"
            )

        # narrative_directive 覆盖率警告
        total_events = 0
        events_with_directive = 0
        for ch in story_chapters:
            for ev in (ch.get("events") or []):
                if isinstance(ev, dict):
                    total_events += 1
                    if ev.get("narrative_directive"):
                        events_with_directive += 1
        directive_ratio = events_with_directive / max(total_events, 1)
        if directive_ratio < 0.3:
            print(
                f"  WARNING: narrative_directive 覆盖率仅 {directive_ratio:.0%} "
                f"({events_with_directive}/{total_events})，叙述质量可能不足"
            )

        mainlines = mainlines_data.get("mainlines")
        if not isinstance(mainlines, list):
            raise ValueError("mainlines.json 缺少 mainlines 列表")
        for mainline in mainlines:
            if not isinstance(mainline, dict):
                continue
            mainline_id = mainline.get("id", "unknown")
            if "chapter_graph" not in mainline:
                raise ValueError(f"strict-v2 校验失败: mainline={mainline_id} 缺少 chapter_graph")
            if not isinstance(mainline.get("chapter_graph"), dict):
                raise ValueError(f"strict-v2 校验失败: mainline={mainline_id} chapter_graph 非对象")


# ==================== Helper Functions ====================


def generate_world_map(maps_data: MapsData) -> WorldMap:
    """
    从地图数据生成世界地图结构（纯规则逻辑，提取自 WorldbookGraphizer）

    根据地图的 region 字段自动分组
    """
    import re

    def _to_id(name: str) -> str:
        cleaned = re.sub(r'[^\w\s]', '', name)
        return cleaned.replace(' ', '_').lower()

    # 按 region 分组
    region_maps: Dict[str, list] = {}
    for m in maps_data.maps:
        region = m.region or "未知区域"
        if region not in region_maps:
            region_maps[region] = []
        region_maps[region].append(m)

    # 生成区域
    danger_levels = {"low": 0, "medium": 1, "high": 2, "extreme": 3}
    reverse_danger = {v: k for k, v in danger_levels.items()}

    regions = []
    for region_name, maps in region_maps.items():
        max_danger = max(danger_levels.get(m.danger_level, 0) for m in maps)
        danger_level = reverse_danger.get(max_danger, "low")

        regions.append(WorldMapRegion(
            id=_to_id(region_name),
            name=region_name,
            description=f"{region_name}，包含 {len(maps)} 个地点",
            maps=[m.id for m in maps],
            danger_level=danger_level,
        ))

    # 收集跨区域连接
    region_by_map = {m.id: m.region or "未知区域" for m in maps_data.maps}
    global_connections = []
    for m in maps_data.maps:
        for conn in m.connections:
            source_region = region_by_map.get(m.id)
            target_region = region_by_map.get(conn.target_map_id)
            if source_region and target_region and source_region != target_region:
                global_connections.append({
                    "from": m.id,
                    "to": conn.target_map_id,
                    "from_region": source_region,
                    "to_region": target_region,
                    "type": conn.connection_type,
                })

    return WorldMap(
        name="游戏世界",
        description="从世界书自动生成的世界地图",
        regions=regions,
        global_connections=global_connections,
    )


def _save_json(path: Path, data: Any, default=None) -> None:
    """保存 JSON 文件"""
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=default),
        encoding="utf-8",
    )
