"""
地图数据加载器

将地图/箱庭数据加载到 Firestore。
"""
import json
from pathlib import Path
from typing import Dict, Any, List, Optional

from google.cloud import firestore

from app.config import settings
from app.tools.worldbook_graphizer.models import MapsData, MapInfo, PasserbyTemplate, SubLocationInfo


class MapLoader:
    """地图数据加载器"""

    def __init__(self, firestore_client: Optional[firestore.Client] = None):
        """
        初始化加载器

        Args:
            firestore_client: Firestore 客户端（可选）
        """
        self.db = firestore_client or firestore.Client(
            database=settings.firestore_database
        )

    def _get_map_ref(
        self,
        world_id: str,
        map_id: str
    ) -> firestore.DocumentReference:
        """获取地图文档引用"""
        return (
            self.db.collection("worlds")
            .document(world_id)
            .collection("maps")
            .document(map_id)
        )

    def _get_world_meta_ref(self, world_id: str) -> firestore.DocumentReference:
        """获取世界元数据文档引用"""
        return (
            self.db.collection("worlds")
            .document(world_id)
            .collection("meta")
            .document("info")
        )

    async def load_maps(
        self,
        world_id: str,
        maps_data: MapsData,
        dry_run: bool = False,
        verbose: bool = True,
    ) -> Dict[str, Any]:
        """
        加载所有地图数据到 Firestore

        Args:
            world_id: 世界 ID
            maps_data: 地图数据
            dry_run: 是否只模拟执行
            verbose: 是否输出详细信息

        Returns:
            加载结果统计
        """
        stats = {
            "maps_loaded": 0,
            "passerby_templates_loaded": 0,
            "errors": [],
        }

        for map_info in maps_data.maps:
            try:
                if verbose:
                    print(f"  Loading map: {map_info.id} ({map_info.name})")

                if not dry_run:
                    await self._load_single_map(world_id, map_info)

                # 加载该地图的路人模板
                templates = maps_data.passerby_templates.get(map_info.id, [])
                if templates:
                    if verbose:
                        print(f"    - {len(templates)} passerby templates")
                    if not dry_run:
                        await self._load_passerby_templates(
                            world_id, map_info.id, templates
                        )
                    stats["passerby_templates_loaded"] += len(templates)

                stats["maps_loaded"] += 1

            except Exception as e:
                error_msg = f"Failed to load map {map_info.id}: {str(e)}"
                stats["errors"].append(error_msg)
                if verbose:
                    print(f"    ERROR: {error_msg}")

        return stats

    async def _load_single_map(
        self,
        world_id: str,
        map_info: MapInfo,
    ) -> None:
        """加载单个地图"""
        map_ref = self._get_map_ref(world_id, map_info.id)

        # 构建地图信息文档
        info_data = {
            "name": map_info.name,
            "description": map_info.description,
            "atmosphere": map_info.atmosphere,
            "danger_level": map_info.danger_level,
            "region": map_info.region,
            "connections": [
                {
                    "target_map_id": c.target_map_id,
                    "connection_type": c.connection_type,
                    "travel_time": c.travel_time,
                    "requirements": c.requirements,
                }
                for c in map_info.connections
            ],
            "available_actions": map_info.available_actions,
            "key_features": map_info.key_features,
            # 新增：子地点信息
            "sub_locations": [
                {
                    "id": sl.id,
                    "name": sl.name,
                    "description": sl.description,
                    "interaction_type": sl.interaction_type,
                    "resident_npcs": sl.resident_npcs,
                    "available_actions": sl.available_actions,
                    "passerby_spawn_rate": sl.passerby_spawn_rate,
                    "travel_time_minutes": sl.travel_time_minutes,
                }
                for sl in map_info.sub_locations
            ],
        }

        # 保存到 maps/{map_id}/info
        map_ref.collection("info").document("data").set(info_data)

    async def _load_passerby_templates(
        self,
        world_id: str,
        map_id: str,
        templates: List[PasserbyTemplate],
    ) -> None:
        """加载路人模板"""
        map_ref = self._get_map_ref(world_id, map_id)
        templates_ref = map_ref.collection("npcs").document("passerby_templates")

        templates_data = {
            "templates": [
                {
                    "template_id": t.template_id,
                    "name_pattern": t.name_pattern,
                    "personality_template": t.personality_template,
                    "speech_pattern": t.speech_pattern,
                    "appearance_hints": t.appearance_hints,
                }
                for t in templates
            ]
        }

        templates_ref.set(templates_data)

    async def load_world_map(
        self,
        world_id: str,
        world_map_path: Path,
        dry_run: bool = False,
        verbose: bool = True,
    ) -> None:
        """
        加载世界地图数据

        Args:
            world_id: 世界 ID
            world_map_path: world_map.json 文件路径
            dry_run: 是否只模拟执行
            verbose: 是否输出详细信息
        """
        if not world_map_path.exists():
            if verbose:
                print(f"  World map file not found: {world_map_path}")
            return

        world_map_data = json.loads(world_map_path.read_text(encoding="utf-8"))

        if verbose:
            print(f"  Loading world map: {world_map_data.get('name', 'Unknown')}")
            print(f"    - {len(world_map_data.get('regions', []))} regions")

        if not dry_run:
            meta_ref = self._get_world_meta_ref(world_id)
            meta_ref.set({
                "name": world_map_data.get("name", world_id),
                "description": world_map_data.get("description", ""),
                "world_map": world_map_data,
            }, merge=True)

    async def get_map_info(
        self,
        world_id: str,
        map_id: str,
    ) -> Optional[Dict[str, Any]]:
        """获取地图信息"""
        map_ref = self._get_map_ref(world_id, map_id)
        doc = map_ref.collection("info").document("data").get()
        if not doc.exists:
            return None
        return doc.to_dict()

    async def list_maps(self, world_id: str) -> List[str]:
        """列出所有地图 ID"""
        maps_ref = (
            self.db.collection("worlds")
            .document(world_id)
            .collection("maps")
        )
        return [doc.id for doc in maps_ref.stream()]

    async def get_passerby_templates(
        self,
        world_id: str,
        map_id: str,
    ) -> List[Dict[str, Any]]:
        """获取地图的路人模板"""
        map_ref = self._get_map_ref(world_id, map_id)
        doc = map_ref.collection("npcs").document("passerby_templates").get()
        if not doc.exists:
            return []
        data = doc.to_dict() or {}
        return data.get("templates", [])
