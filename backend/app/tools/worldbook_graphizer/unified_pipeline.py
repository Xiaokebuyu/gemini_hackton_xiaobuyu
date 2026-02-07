"""
统一世界书提取管线

从 SillyTavern V2 Lorebook JSON 一步生成 WorldInitializer 所需的全部文件：
  maps.json, characters.json, world_map.json, character_profiles.json,
  world_graph.json, prefilled_graph.json, chapters_v2.json

世界图谱提取支持两种模式：
- Batch API（默认）：50% 成本优惠，适合大量条目
- 直接调用（--direct）：实时返回，无需等待批量任务

用法:
    python -m app.tools.init_world_cli extract \
        --input data/gs/worldbook.json \
        --output data/gs/structured/
    python -m app.tools.init_world_cli extract \
        --input data/gs/worldbook.json \
        --output data/gs/structured/ --direct
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
    ) -> Dict[str, Any]:
        """
        执行统一提取管线

        Args:
            lorebook_path: SillyTavern V2 Lorebook JSON 路径
            output_dir: 输出目录
            mainlines_path: 可选的 mainlines.json 路径
            validate: 是否验证中间结果
            use_direct: 使用直接 LLM 调用而非 Batch API

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
            _save_json(output_dir / "mainlines.json", mainlines_data)
            self._log(f"  Copied mainlines.json ({len(mainlines_data.get('chapters', []))} chapters)")
        else:
            story_entries = self.parser.get_entries_by_types(data, ["story"])
            if story_entries:
                self._log(f"\n[Step 7b] Generating mainlines from {len(story_entries)} story entries...")
                mainlines_data = await self._generate_mainlines(story_entries, maps_data)
                _save_json(output_dir / "mainlines.json", mainlines_data)
                self._log(f"  Generated mainlines.json ({len(mainlines_data.get('mainlines', []))} volumes, {len(mainlines_data.get('chapters', []))} chapters)")
            else:
                self._log("  No story entries found, skipping mainlines generation")

        # ── Step 8: GraphPrefiller ──
        self._log("\n[Step 8] Running GraphPrefiller...")
        prefiller = GraphPrefiller(output_dir)
        prefill_result = prefiller.run(verbose=self.verbose)
        prefiller.save(prefill_result, output_dir)

        stats["prefill_nodes"] = len(prefill_result.nodes)
        stats["prefill_edges"] = len(prefill_result.edges)
        stats["chapters"] = len(prefill_result.chapters_v2)
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

    async def _generate_mainlines(
        self,
        story_entries: list,
        maps_data: MapsData,
    ) -> Dict[str, Any]:
        """从故事类条目生成 mainlines.json

        Phase 1（纯规则）：从 entry.comment 提取卷/章结构
        Phase 2（LLM）：生成 available_maps、objectives 等
        """
        # Phase 1: 用正则提取卷/章结构
        volumes: Dict[str, Dict[str, Any]] = {}   # vol_id -> {name, chapters: []}
        chapters: List[Dict[str, Any]] = []

        # 对 story_entries 按 comment 排序确保顺序
        sorted_entries = sorted(story_entries, key=lambda e: e.order)

        vol_pattern = re.compile(r'第(\d+|[一二三四五六七八九十百]+)卷')
        ch_pattern = re.compile(r'第(\d+|[一二三四五六七八九十百]+)章')

        current_vol_id = "vol_1"
        current_vol_name = "第一卷"

        for entry in sorted_entries:
            comment = entry.comment or ""
            content_preview = (entry.content or "")[:500]

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

            # 提取卷号数字用于章节 ID
            vol_num_str = current_vol_id.removeprefix("vol_")
            ch_id = f"ch_{vol_num_str}_{ch_num_int}"

            chapter_info = {
                "id": ch_id,
                "mainline_id": current_vol_id,
                "name": ch_name,
                "description": content_preview,
                "available_maps": [],
                "objectives": [],
                "trigger_conditions": {},
                "completion_conditions": {},
            }
            chapters.append(chapter_info)
            volumes[current_vol_id]["chapters"].append(ch_id)

        # Phase 2: LLM 填充 available_maps 和 objectives
        if chapters and maps_data and maps_data.maps:
            try:
                chapters = await self._enrich_mainlines_with_llm(
                    chapters, volumes, maps_data
                )
            except Exception as e:
                self._log(f"  Warning: LLM enrichment failed, using rule-based only: {e}")

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
