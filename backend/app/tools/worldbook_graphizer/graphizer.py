"""
[DEPRECATED] 世界书图谱化主编排器 — 已被 unified_pipeline.py 替代。

请使用 `python -m app.tools.init_world_cli extract` 命令。
本文件仅被旧 CLI 命令 (graphize/graphize-maps/graphize-characters) 调用。
"""
import json
from pathlib import Path
from typing import Optional, Dict, Any

from .models import (
    MapsData,
    CharactersData,
    WorldMap,
    WorldMapRegion,
    GraphizeResult,
)
from .map_extractor import MapExtractor
from .npc_classifier import NPCClassifier


class WorldbookGraphizer:
    """世界书图谱化器"""

    def __init__(self, model: str = None, api_key: str = None):
        """
        初始化图谱化器

        Args:
            model: Gemini 模型名称
            api_key: API 密钥
        """
        self.model = model or "gemini-2.0-flash"
        self.api_key = api_key

        self.map_extractor = MapExtractor(model=self.model, api_key=self.api_key)
        self.npc_classifier = NPCClassifier(model=self.model, api_key=self.api_key)

    async def graphize(
        self,
        worldbook_path: Path,
        output_dir: Path,
        validate: bool = True,
        verbose: bool = True,
    ) -> GraphizeResult:
        """
        执行完整的图谱化流程

        Args:
            worldbook_path: 世界书文件路径
            output_dir: 输出目录
            validate: 是否验证结果
            verbose: 是否输出详细信息

        Returns:
            GraphizeResult: 图谱化结果
        """
        # 加载世界书
        if verbose:
            print(f"Loading worldbook from {worldbook_path}...")
        worldbook_content = worldbook_path.read_text(encoding="utf-8")
        if verbose:
            print(f"  Content size: {len(worldbook_content):,} characters")

        # 阶段 1: 提取地图
        if verbose:
            print("\n[Phase 1] Extracting maps...")
        maps_data = await self.map_extractor.extract(worldbook_content)
        if verbose:
            print(f"  Found {len(maps_data.maps)} maps")
            for m in maps_data.maps[:5]:
                print(f"    - {m.id}: {m.name}")
            if len(maps_data.maps) > 5:
                print(f"    ... and {len(maps_data.maps) - 5} more")

        # 验证地图
        if validate:
            map_errors = self.map_extractor.validate(maps_data)
            if map_errors:
                print(f"  Warnings: {len(map_errors)} validation issues")
                for err in map_errors[:3]:
                    print(f"    - {err}")

        # 阶段 2: 分类 NPC
        if verbose:
            print("\n[Phase 2] Classifying NPCs...")
        characters_data = await self.npc_classifier.classify(
            worldbook_content, maps_data
        )
        if verbose:
            main_count = len([c for c in characters_data.characters if c.tier.value == "main"])
            secondary_count = len([c for c in characters_data.characters if c.tier.value == "secondary"])
            passerby_count = len([c for c in characters_data.characters if c.tier.value == "passerby"])
            print(f"  Found {len(characters_data.characters)} characters:")
            print(f"    - Main: {main_count}")
            print(f"    - Secondary: {secondary_count}")
            print(f"    - Passerby: {passerby_count}")

        # 验证角色
        if validate:
            char_errors = self.npc_classifier.validate(characters_data, maps_data)
            if char_errors:
                print(f"  Warnings: {len(char_errors)} validation issues")
                for err in char_errors[:3]:
                    print(f"    - {err}")

        # 阶段 3: 生成世界地图
        if verbose:
            print("\n[Phase 3] Generating world map...")
        world_map = self._generate_world_map(maps_data)
        if verbose:
            print(f"  Regions: {len(world_map.regions)}")

        # 组装结果
        result = GraphizeResult(
            maps=maps_data,
            characters=characters_data,
            world_map=world_map,
            metadata={
                "source_file": str(worldbook_path),
                "source_size": len(worldbook_content),
                "model": self.model,
            }
        )

        # 保存输出
        if verbose:
            print(f"\n[Phase 4] Saving outputs to {output_dir}...")
        output_dir.mkdir(parents=True, exist_ok=True)
        self._save_outputs(result, output_dir)

        if verbose:
            print("\nGraphization complete!")
            print(f"  Maps: {len(maps_data.maps)}")
            print(f"  Characters: {len(characters_data.characters)}")
            print(f"  Regions: {len(world_map.regions)}")

        return result

    def _generate_world_map(self, maps_data: MapsData) -> WorldMap:
        """
        从地图数据生成世界地图结构

        根据地图的 region 字段自动分组
        """
        # 按 region 分组
        region_maps: Dict[str, list] = {}
        for m in maps_data.maps:
            region = m.region or "未知区域"
            if region not in region_maps:
                region_maps[region] = []
            region_maps[region].append(m)

        # 生成区域
        regions = []
        for region_name, maps in region_maps.items():
            # 计算区域危险等级（取最高）
            danger_levels = {"low": 0, "medium": 1, "high": 2, "extreme": 3}
            max_danger = max(
                danger_levels.get(m.danger_level, 0) for m in maps
            )
            danger_level = {v: k for k, v in danger_levels.items()}.get(max_danger, "low")

            region_id = self._to_id(region_name)
            regions.append(WorldMapRegion(
                id=region_id,
                name=region_name,
                description=f"{region_name}，包含 {len(maps)} 个地点",
                maps=[m.id for m in maps],
                danger_level=danger_level,
            ))

        # 收集全局连接（跨区域）
        global_connections = []
        region_by_map = {m.id: m.region or "未知区域" for m in maps_data.maps}
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

    def _to_id(self, name: str) -> str:
        """将中文名转换为 ID"""
        # 简单转换：移除空格，转小写
        import re
        # 移除特殊字符
        cleaned = re.sub(r'[^\w\s]', '', name)
        # 替换空格为下划线
        return cleaned.replace(' ', '_').lower()

    def _save_outputs(self, result: GraphizeResult, output_dir: Path) -> None:
        """保存输出文件"""
        # maps.json
        maps_path = output_dir / "maps.json"
        maps_path.write_text(
            json.dumps(
                result.maps.model_dump(),
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        # characters.json
        chars_path = output_dir / "characters.json"
        chars_path.write_text(
            json.dumps(
                result.characters.model_dump(),
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        # world_map.json
        world_map_path = output_dir / "world_map.json"
        world_map_path.write_text(
            json.dumps(
                result.world_map.model_dump(),
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        # character_profiles.json (用于直接导入 Firestore)
        profiles = self.npc_classifier.to_character_profiles(result.characters)
        profiles_path = output_dir / "character_profiles.json"
        profiles_path.write_text(
            json.dumps(profiles, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        # metadata.json
        meta_path = output_dir / "metadata.json"
        meta_path.write_text(
            json.dumps(result.metadata, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    async def graphize_maps_only(
        self,
        worldbook_path: Path,
        output_path: Path,
    ) -> MapsData:
        """仅提取地图数据"""
        worldbook_content = worldbook_path.read_text(encoding="utf-8")
        maps_data = await self.map_extractor.extract(worldbook_content)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(maps_data.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        return maps_data

    async def graphize_characters_only(
        self,
        worldbook_path: Path,
        maps_path: Optional[Path],
        output_path: Path,
    ) -> CharactersData:
        """仅提取角色数据"""
        worldbook_content = worldbook_path.read_text(encoding="utf-8")

        maps_data = None
        if maps_path and maps_path.exists():
            maps_raw = json.loads(maps_path.read_text(encoding="utf-8"))
            maps_data = MapsData(**maps_raw)

        characters_data = await self.npc_classifier.classify(
            worldbook_content, maps_data
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(characters_data.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        return characters_data
